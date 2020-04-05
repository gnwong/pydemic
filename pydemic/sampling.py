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


class SampleParameter:
    def __init__(self, name, bounds, guess, uncertainty):
        self.name = name
        self.bounds = bounds
        self.guess = guess
        self.uncertainty = uncertainty


def l2_log_norm(a, b):
    return -1/2 * np.sum(np.power(np.log(a)-np.log(b), 2.))


class LikelihoodEstimatorBase:
    def __init__(self, fit_parameters, fixed_values, data, norm=None):
        self.fit_parameters = fit_parameters
        self.fit_names = tuple(par.name for par in fit_parameters)
        self.fixed_values = fixed_values
        self.data = data.copy()

        if norm is None:
            self.norm = l2_log_norm
        else:
            self.norm = norm

    def check_within_bounds(self, theta):
        for par, value in zip(self.fit_parameters, theta):
            bounds = par.bounds
            if not bounds[0] <= value <= bounds[1]:
                return False
        return True

    def __call__(self, theta):
        if not self.check_within_bounds(theta):
            return -np.inf
        else:
            parameters = dict(zip(self.fit_names, theta))
            return self.get_log_likelihood(parameters)

    def get_log_likelihood(self, parameters):
        raise NotImplementedError

    def get_initial_positions(self, walkers):
        init = np.array([par.guess + np.random.randn(walkers) * par.uncertainty
                         for par in self.fit_parameters])
        return init.T

    def sample_uniform(self, num_points, pool=None):
        if not isinstance(num_points, dict):
            num_points = {par.name: num_points for par in self.fit_parameters}

        samples = {
            par.name: list(np.linspace(*par.bounds, num_points[par.name]))
            for par in self.fit_parameters
        }

        from itertools import product
        all_value_sets = product(*[sample for sample in samples.values()])
        values = [values for values in all_value_sets]

        if pool is not None:
            likelihoods = pool.map(self.__call__, values)
        else:
            likelihoods = [self.__call__(value) for value in values]

        return np.array(values), np.array(likelihoods)

    def sample_emcee(self, steps, walkers=None, pool=None, moves=None,
                     checkpoint_steps=100, backend_filename=None, progress=True):
        if pool is None:
            from multiprocessing import Pool
            pool = Pool()
        if walkers is None:
            walkers = pool._processes

        import emcee

        initial_positions = self.get_initial_positions(walkers)
        ndim = initial_positions.shape[-1]

        if backend_filename is not None:
            backend = emcee.backends.HDFBackend(backend_filename)
            backend.reset(walkers, ndim)
        else:
            backend = None

        sampler = emcee.EnsembleSampler(walkers, ndim, self, moves=moves,
                                        backend=backend, pool=pool)

        index = 0
        autocorrelations = np.zeros((int(steps//checkpoint_steps)+1, ndim))
        for sample in sampler.sample(initial_positions, iterations=steps,
                                     progress=progress):
            if sampler.iteration % checkpoint_steps:
                continue

            tau = sampler.get_autocorr_time(tol=0)
            autocorrelations[index, :] = np.mean(tau)
            index += 1

            # FIXME: automatic convergence doesn't work .. odd array shapes?
            print(autocorrelations[index-1])

        return sampler, autocorrelations