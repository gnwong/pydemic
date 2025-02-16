__copyright__ = """
Copyright (C) 2020 George N Wong
Copyright (C) 2020 Zachary J Weiner
"""

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
# pylint: disable=no-member


class SimulationResult:
    def __init__(self, time, y):
        self.t = time
        self.y = y


from pydemic.distributions import GammaDistribution
default_serial = GammaDistribution(mean=4, std=3.25)


class NonMarkovianSEIRSimulationBase:
    """
    Main driver for non-Markovian simulations, used as a base class for
    SEIR++ variants.

    .. automethod:: __init__
    .. automethod:: get_y0
    .. automethod:: __call__
    .. automethod:: get_model_data
    """

    increment_keys = ('infected', 'dead')

    def seasonal_forcing(self, t):
        phase = 2 * np.pi * (t - self.peak_day) / 365
        return (1 + self.seasonal_forcing_amp * np.cos(phase))

    def __init__(self, mitigation=None, *, age_distribution=None,
                 r0=3.2, serial_dist=default_serial,
                 seasonal_forcing_amp=.2, peak_day=15):
        """
        The following keyword-only arguments are recognized:

        :arg mitigation: A function of time specifying a multiplicative factor.
            Defaults to ``lambda t: 1``.

        :arg r0: The basic reproduction number.

        :arg serial_dist: The serial interval distribution, i.e.,
            an instance of (a subclass of)
            :class:`~pydemic.distributions.DistributionBase`).

        :arg seasonal_forcing_amp: The amplitude (i.e., maximum fractional change)
            in the force of infection due to seasonal effects.

        :arg peak_day: The day of the year at which seasonal forcing is greatest.
        """

        if mitigation is not None:
            self.mitigation = mitigation
        else:
            self.mitigation = lambda t: 1

        self.serial_dist = serial_dist
        self.seasonal_forcing_amp = seasonal_forcing_amp
        self.peak_day = peak_day
        self.r0 = r0

    def step(self, state, count, dt):
        Rt = (self.r0
              * self.mitigation(state.t[count])
              * self.seasonal_forcing(state.t[count]))
        j_m = np.dot(state.y['infected'][..., count-1::-1],
                     self.serial_pdf[:count])
        j_i = j_m.sum()
        S_i = state.y['susceptible'][..., count-1]
        new_infected_i = dt * Rt * S_i * j_i / self.total_population
        state.y['infected'][..., count] = new_infected_i

        state.y['susceptible'][..., count] = (
            state.y['susceptible'][..., count-1] - new_infected_i
        )

    def get_y0(self, total_population, initial_cases, age_distribution):
        """
        Initializes a population with a number ``initial_cases`` of initial
        infectious individuals distributed in proportion to ``age_distribution``.

        :arg total_population: The total size of the population.

        :arg initial_cases: The total numnber of initial cases.

        :arg age_distribution: A :class:`numpy.ndarray` specifying the relative
            fraction of the population in various age groups.

        :returns: A :class:`dict` containing the initial conditions for the
            ``'infected'`` and ``'susceptible'`` compartments.
        """

        # FIXME: shouldn't be set here
        self.total_population = total_population
        self.population = total_population * np.array(age_distribution)
        n_demographics = len(age_distribution)

        y0 = {}
        for key in ('susceptible', 'infected'):
            y0[key] = np.zeros((n_demographics,))

        y0['infected'][...] = initial_cases * np.array(age_distribution)
        y0['susceptible'][...] = self.population - y0['infected']

        return y0

    def __call__(self, tspan, y0, dt=.05):
        """
        :arg tspan: A :class:`tuple` specifying the initiala and final times.

        :arg y0: A :class:`dict` with the initial values
            (as :class:`numpy.ndarray`'s) for the
            ``'infected'`` and ``'susceptible'`` compartments, e.g., as returned
            by :meth:`get_y0`.

        :arg dt: The timestep.

        :returns: A :class:`SimulationResult` with attributes ``t``, the array of
            times of evaluation, and ``y``, a :class:`dict` of results where time
            proceeds along the first axis.
        """

        start_time, end_time = tspan
        times = np.arange(start_time, end_time + dt, dt)
        n_steps = times.shape[0]
        pdf = self.serial_dist.pdf(times[1:] - start_time, method='diff')
        self.serial_pdf = pdf / dt

        y0_all_t = {}
        for key in y0:
            y0_all_t[key] = np.zeros(y0[key].shape + (n_steps,))
            y0_all_t[key][..., 0] = y0[key]

        influxes = SimulationResult(times, y0_all_t)

        for count in range(1, n_steps):
            self.step(influxes, count, dt)

        for key, val in influxes.y.items():
            influxes.y[key] = val.T

        return influxes

    @classmethod
    def get_model_data(cls, t, **kwargs):
        """
        A wrapper to :meth:`__init__` and :meth:`__call__` for initializing and
        running a simulation from keyword arguments only (i.e., as used by
        :class:`~pydemic.LikelihoodEstimator`.)

        :arg t: A :class:`pandas.DatetimeIndex` (or :class:`numpy.ndarray` of
            times in days since 2020/1/1) of times at which to evaluate the
            solution.

        The following keyword arguments are required:

        :arg start_day: The day (relative to 2020/1/1) at which to begin the
            simulation---i.e., the day corresponding to the initial condition
            generated by :meth:`get_y0`.

        :arg total_population: The total size of the population.

        :arg initial_cases: The total numnber of initial cases.

        :arg age_distribution: A :class:`numpy.ndarray` specifying the relative
            fraction of the population in various age groups.

        In addition, a :class:`~pydemic.MitigationModel` is created from
        passed keyword arguments via
        :meth:`~pydemic.MitigationModel.init_from_kwargs`.

        The following optional keyword arguments are also recognized:

        :arg min_mitigation_spacing: The minimum number of days of separation
            between mitigation events.
            Defaults to ``5``.

        All remaining keyword arguments are passed to :meth:`__init__`.

        :raises InvalidParametersError: if ``t`` specifies any days of evaluation
            which are not at least one day after ``start_day``.

        :raises InvalidParametersError: if mitigation events are not ordered and
            separated by ``min_mitigation_spacing``.

        :returns: A :class:`pandas.DataFrame` of simulation results.
        """

        if isinstance(t, pd.DatetimeIndex):
            t_eval = (t - pd.to_datetime('2020-01-01')) / pd.Timedelta('1D')
        else:
            t_eval = t

        t0 = kwargs.pop('start_day')
        tf = t_eval[-1] + 2

        from pydemic.sampling import InvalidParametersError

        if (t_eval < t0 + 1).any():
            raise InvalidParametersError(
                "Must start simulation at least one day before result evaluation."
            )

        if 'log_ifr' in kwargs:
            if 'ifr' in kwargs:
                raise InvalidParametersError("Can't pass both ifr and log_ifr.")
            kwargs['ifr'] = np.exp(kwargs.pop('log_ifr'))

        try:
            from pydemic.mitigation import MitigationModel
            mitigation = MitigationModel.init_from_kwargs(t0, tf, **kwargs)
            for key in list(kwargs.keys()):
                if 'mitigation_t' in key or 'mitigation_factor' in key:
                    _ = kwargs.pop(key)
        except ValueError:  # raised by PchipInterpolator when times aren't ordered
            raise InvalidParametersError(
                "Mitigation times must be ordered within t0 and tf."
            )

        if any(np.diff(mitigation.times) < kwargs.pop('min_mitigation_spacing', 5)):
            raise InvalidParametersError(
                "Mitigation times must be spaced by at least min_mitigation_spacing."
                " Decrease min_mitigation_spacing to prevent this check."
            )

        age_distribution = kwargs.pop('age_distribution')

        for key in ('serial', 'incubation', 'hospitalized', 'discharged',
                    'critical', 'dead', 'recovered', 'all_dead'):
            mean = kwargs.pop(key+'_mean', None)
            std = kwargs.pop(key+'_std', None)
            shape = kwargs.pop(key+'_k', None)
            # only pass key_dist if key_mean and one of key_std/key_k are passed
            if mean is not None and shape is not None:
                kwargs[key+'_dist'] = GammaDistribution(mean=mean, shape=shape)
            elif mean is not None and std is not None:
                kwargs[key+'_dist'] = GammaDistribution(mean=mean, std=std)
            elif mean is not None:
                raise InvalidParametersError(
                    "Must pass either %s+_shape or %s+_std." % (key, key)
                )

        for key in ('symptomatic', 'positive', 'hospitalized', 'critical', 'dead'):
            # FIXME: decide on default behavior here
            prefactor = kwargs.pop('p_'+key+'_prefactor', None)
            if prefactor is not None:
                prob = np.array(kwargs.get('p_'+key, 1.)).copy()
                kwargs['p_'+key] = prefactor * prob

        total_population = kwargs.pop('total_population')
        initial_cases = kwargs.pop('initial_cases')

        sim = cls(
            mitigation=mitigation, age_distribution=age_distribution, **kwargs
        )

        y0 = sim.get_y0(total_population, initial_cases, age_distribution)
        result = sim((t0, tf), y0)

        y = {}
        for key, val in result.y.items():
            y[key] = interp1d(result.t, val.sum(axis=-1), axis=0)(t_eval)

        for key in cls.increment_keys:
            if key in result.y.keys():
                spline = interp1d(result.t, result.y[key].sum(axis=-1), axis=0)
                y[key+'_incr'] = spline(t_eval) - spline(t_eval - 1)

        _t = pd.to_datetime(t_eval, origin='2020-01-01', unit='D')
        return pd.DataFrame(y, index=_t)


class SEIRPlusPlusSimulation(NonMarkovianSEIRSimulationBase):
    """
    SEIR++ model with unconnected infectivity loop. Readout topology is::

        -> symptomatic
            -> hospitalized -> recovered
                            -> critical -> dead -> all_dead
                                        -> hospitalized -> recovered

    .. automethod:: __init__
    """

    increment_keys = ('infected', 'dead', 'all_dead', 'positive',
                      'admitted_to_hospital', 'total_discharged')

    def __init__(self, mitigation=None, *, age_distribution, ifr=None,
                 r0=3.2, serial_dist=default_serial,
                 seasonal_forcing_amp=.2, peak_day=15,
                 incubation_dist=GammaDistribution(5.5, 2),
                 p_symptomatic=1., p_positive=1.,
                 hospitalized_dist=GammaDistribution(6.5, 4), p_hospitalized=1.,
                 discharged_dist=GammaDistribution(6, 4),
                 critical_dist=GammaDistribution(2, 2), p_critical=1.,
                 dead_dist=GammaDistribution(7.5, 7.5), p_dead=1.,
                 recovered_dist=GammaDistribution(7.5, 7.5),
                 all_dead_dist=GammaDistribution(2.5, 2.5), all_dead_multiplier=1.):
        """
        In addition to the arguments recognized by
        :class:`~pydemic.models.seirpp.NonMarkovianSEIRSimulationBase`, the
        following keyword-only arguments are recognized:

        :arg age_distribution: A :class:`numpy.ndarray` specifying the relative
            fraction of the population in various age groups.

        :arg ifr: The infection fatality ratio, i.e., the proportion of the infected
            population who eventually die.
            If not *None*, will rescale ``p_symptomatic`` to effect the passed value.

        :arg p_symptomatic: The distribution of the proportion of infected
            individuals who become symptomatic.

        :arg incubation_dist: The delay-time distribution
            for developing symptoms after being infected.

        :arg p_positive: The fraction of symptomatic individuals who are tested and
            test positive.

        :arg p_hospitalized: The distribution of the proportion of symptomatic
            individuals who enter the hospital.

        :arg hospitalized_dist: The delay-time distribution of
            entering the hospital after becoming symptomatic.

        :arg discharged_dist: The delay-time distribution of
            survivors being discharged after entering the hospital.

        :arg p_critical: The distribution of the proportion of
            hospitalized individuals who become critical.

        :arg critical_dist: The delay-time distribution
            of hospitalized individuals entering the ICU.

        :arg p_dead: The distribution of the proportion of
            ICU patients who die.

        :arg dead_dist: The delay-time distribution of ICU patients dying.

        :arg recovered_dist: The delay-time distribution
            of ICU patients recovering and returning to the general ward.

        :arg all_dead_multiplier: The ratio of total deaths to deaths occurring
            in the ICU.

        :arg all_dead_dist: The delay-time distribution
            between ICU deaths and all reported deaths.
        """

        super().__init__(
            mitigation=mitigation, r0=r0,
            serial_dist=serial_dist,
            seasonal_forcing_amp=seasonal_forcing_amp, peak_day=peak_day
        )

        age_distribution = np.array(age_distribution)
        p_symptomatic = np.array(p_symptomatic)
        p_hospitalized = np.array(p_hospitalized)
        p_critical = np.array(p_critical)
        p_dead = np.array(p_dead)

        # if p_symptomatic is None, set so that
        # p_symptomatic * p_hospitalized * p_critical * p_dead
        # weighted by the age distribution, achieves ifr
        if ifr is not None:
            p_dead_net = (
                p_symptomatic * p_hospitalized * p_critical
                * p_dead * all_dead_multiplier
            )
            weighted_sum = (p_dead_net * age_distribution).sum()
            p_symptomatic *= ifr / weighted_sum

        # check that none of the probabilties are too large
        from pydemic.sampling import InvalidParametersError

        p_progression = [age_distribution]
        for prob in (p_symptomatic, p_hospitalized, p_critical, p_dead):
            p_progression.append(p_progression[-1] * prob)

        names = ('p_symptomatic', 'p_hospitalized', 'p_critical', 'p_dead')
        for i, name in enumerate(names):
            if p_progression[i].sum() < p_progression[i+1].sum():
                raise InvalidParametersError("%s is too large" % name)

        self.readouts = {
            "symptomatic": ('infected', p_symptomatic, incubation_dist),
            "positive": ('infected', p_positive * p_symptomatic, incubation_dist),
            "admitted_to_hospital": ('symptomatic', p_hospitalized,
                                     hospitalized_dist),
            "icu": ('admitted_to_hospital', p_critical, critical_dist),
            "dead": ('icu', p_dead, dead_dist),
            "general_ward": ('icu', 1.-p_dead, recovered_dist),
            "hospital_recovered": ('admitted_to_hospital', 1.-p_critical,
                                   discharged_dist),
            "general_ward_recovered": ('general_ward', 1., discharged_dist),
            "all_dead": ('dead', all_dead_multiplier, all_dead_dist),
        }

    def __call__(self, tspan, y0, dt=.05):
        influxes = super().__call__(tspan, y0, dt=dt)
        t = influxes.t

        for key, (src, prob, dist) in self.readouts.items():
            influxes.y[key] = dist.convolve_pdf(t, influxes.y[src], prob)

        sol = SimulationResult(t, {})

        for key, val in influxes.y.items():
            if key not in ["susceptible", "population"]:
                sol.y[key] = np.cumsum(val, axis=0)
            else:
                sol.y[key] = val

        infectious_dist = GammaDistribution(mean=5, std=2)
        sol.y['infectious'] = infectious_dist.convolve_survival(
            t, influxes.y['infected']
        )

        sol.y['critical'] = sol.y['icu'] - sol.y['general_ward'] - sol.y['dead']
        sol.y['ventilators'] = .73 * sol.y['critical']
        sol.y['hospitalized'] = (
            sol.y['admitted_to_hospital']
            - sol.y['hospital_recovered'] - sol.y['icu']
        )
        sol.y['hospitalized'] += (sol.y['general_ward']
                                  - sol.y['general_ward_recovered'])

        sol.y['total_discharged'] = (
            sol.y['hospital_recovered'] + sol.y['general_ward_recovered']
        )
        sol.y['recovered'] = (
            sol.y['infected'] - sol.y['infectious'] - sol.y['all_dead']
        )

        return sol


class SEIRPlusPlusSimulationHospitalCriticalAndDeath(SEIRPlusPlusSimulation):
    def __init__(self, *args, **kwargs):
        from warnings import warn
        warn("pydemic.SEIRPlusPlusSimulationHospitalCriticalAndDeath is deprecated. "
             "Use pydemic.SEIRPlusPlusSimulation instead.",
             DeprecationWarning, stacklevel=2)

        super().__init__(*args, **kwargs)

    @classmethod
    def get_model_data(cls, *args, **kwargs):
        from warnings import warn
        warn("pydemic.SEIRPlusPlusSimulationHospitalCriticalAndDeath is deprecated. "
             "Use pydemic.SEIRPlusPlusSimulation instead.",
             DeprecationWarning, stacklevel=2)

        return super().get_model_data(*args, **kwargs)


class OldSEIRPlusPlusSimulation(NonMarkovianSEIRSimulationBase):
    """
    .. automethod:: __init__
    """

    def __init__(self, mitigation=None, *, age_distribution, ifr=0.003,
                 r0=3.2, serial_dist=default_serial,
                 seasonal_forcing_amp=.2, peak_day=15,
                 incubation_dist=GammaDistribution(5.5, 2), p_observed=1,
                 icu_dist=GammaDistribution(11, 5), p_icu=1,
                 dead_dist=GammaDistribution(7.5, 7.5), p_dead=1,
                 recovered_dist=GammaDistribution(7.5, 7.5),
                 **kwargs):
        """
        In addition to the arguments recognized by
        :class:`~pydemic.models.seirpp.NonMarkovianSEIRSimulationBase`, the
        following keyword-only arguments are recognized:

        :arg age_distribution:

        :arg ifr:

        :arg incubation_dist:

        :arg p_observed:

        :arg icu_dist:

        :arg p_icu:

        :arg dead_dist:

        :arg p_dead:

        :arg recovered_dist:
        """

        super().__init__(
            mitigation=mitigation, r0=r0,
            serial_dist=serial_dist,
            seasonal_forcing_amp=seasonal_forcing_amp, peak_day=peak_day
        )

        p_symptomatic = 1.
        p_symptomatic = np.array(p_symptomatic)
        p_observed = np.array(p_observed)

        # FIXME: this is a kludge-y way to set the target ifr
        # (infection, not just symptomatic)
        p_dead_all = p_symptomatic * p_observed * p_icu * p_dead
        synthetic_ifr = (p_dead_all * age_distribution).sum()
        p_symptomatic *= ifr / synthetic_ifr

        self.readouts = {
            "observed": ('infected', p_observed * p_symptomatic, incubation_dist),
            "icu": ('observed', p_icu, icu_dist),
            "dead": ('icu', p_dead, dead_dist),
            "recovered": ('icu', (1 - p_dead), recovered_dist),
        }

    def __call__(self, tspan, y0, dt=.05):
        influxes = super().__call__(tspan, y0, dt=dt)
        t = influxes.t

        for key, (src, prob, dist) in self.readouts.items():
            influxes.y[key] = dist.convolve_pdf(t, influxes.y[src], prob)

        sol = SimulationResult(t, {})

        for key, val in influxes.y.items():
            if key not in ["susceptible", "population"]:
                sol.y[key] = np.cumsum(val, axis=0)
            else:
                sol.y[key] = val

        infectious_dist = GammaDistribution(mean=5, std=2)
        sol.y['infectious'] = infectious_dist.convolve_survival(
            t, influxes.y['infected']
        )
        sol.y["critical"] = sol.y["icu"] - sol.y["dead"] - sol.y["recovered"]
        sol.y['ventilators'] = .73 * sol.y['critical']

        i = np.searchsorted(sol.t, sol.t[0] + 5)
        sol.y["hospitalized"] = np.zeros_like(sol.y['critical'])
        sol.y["hospitalized"][:-i] = 2.7241 * sol.y['critical'][i:]
        sol.y["hospitalized"][-i:] = np.nan

        return sol


class SEIRPlusPlusSimulationOnsetAndDeath(NonMarkovianSEIRSimulationBase):
    """
    .. automethod:: __init__
    """

    def __init__(self, mitigation=None, *,
                 r0=3.2, serial_dist=default_serial,
                 seasonal_forcing_amp=.2, peak_day=15,
                 p_symptomatic=1,
                 incubation_dist=GammaDistribution(5.5, 2), p_observed=1,
                 dead_dist=GammaDistribution(7.5, 7.5), p_dead=1,
                 **kwargs):
        """
        In addition to the arguments recognized by
        :class:`~pydemic.models.seirpp.NonMarkovianSEIRSimulationBase`, the
        following keyword-only arguments are recognized:

        :arg p_symptomatic:

        :arg incubation_dist:

        :arg p_observed:

        :arg dead_dist:

        :arg p_dead:
        """

        super().__init__(
            mitigation=mitigation, r0=r0,
            serial_dist=serial_dist,
            seasonal_forcing_amp=seasonal_forcing_amp, peak_day=peak_day
        )

        p_symptomatic = np.array(p_symptomatic)
        p_observed = np.array(p_observed)
        p_dead = np.array(p_dead)

        self.readouts = {
            "observed": ('infected', p_observed * p_symptomatic, incubation_dist),
            "dead": ('observed', p_dead, dead_dist),
        }

    def __call__(self, tspan, y0, dt=.05):
        influxes = super().__call__(tspan, y0, dt=dt)
        t = influxes.t

        for key, (src, prob, dist) in self.readouts.items():
            influxes.y[key] = dist.convolve_pdf(t, influxes.y[src], prob)

        sol = SimulationResult(t, {})

        for key, val in influxes.y.items():
            if key not in ["susceptible", "population"]:
                sol.y[key] = np.cumsum(val, axis=0)
            else:
                sol.y[key] = val

        infectious_dist = GammaDistribution(mean=5, std=2)
        sol.y['infectious'] = infectious_dist.convolve_survival(
            t, influxes.y['infected']
        )

        return sol
