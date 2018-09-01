import re
import warnings

import numpy as np
import xarray as xr

from arviz import InferenceData
from arviz.compat import pymc3 as pm


def convert_to_inference_data(obj, *_, group='posterior', coords=None, dims=None):
    """Convert a supported object to an InferenceData object

    This function sends `obj` to the right conversion function. It is idempotent,
    in that it will return arviz.InferenceData objects unchanged.

    Parameters
    ----------
    obj : dict, str, np.ndarray, xr.Dataset, pystan fit, pymc3 trace
        A supported object to convert to InferenceData:
            InferenceData: returns unchanged
            str: Attempts to load the netcdf dataset from disk
            pystan fit: Automatically extracts data
            pymc3 trace: Automatically extracts data
            xarray.Dataset: adds to InferenceData as only group
            dict: creates an xarray dataset as the only group
            numpy array: creates an xarray dataset as the only group, gives the
                         array an arbitrary name
    group : str
        If `obj` is a dict or numpy array, assigns the resulting xarray
        dataset to this group. Default: "posterior".
    coords : dict[str, iterable]
        A dictionary containing the values that are used as index. The key
        is the name of the dimension, the values are the index values.
    dims : dict[str, List(str)]
        A mapping from variables to a list of coordinate names for the variable

    Returns
    -------
    InferenceData
    """
    # Cases that convert to InferenceData
    if isinstance(obj, InferenceData):
        return obj
    elif isinstance(obj, str):
        return InferenceData.from_netcdf(obj)
    elif obj.__class__.__name__ == 'StanFit4Model':  # ugly, but doesn't make PyStan a requirement
        return pystan_to_inference_data(fit=obj, coords=coords, dims=dims)
    elif obj.__class__.__name__ == 'MultiTrace':  # ugly, but doesn't make PyMC3 a requirement
        return pymc3_to_inference_data(trace=obj, coords=coords, dims=dims)

    # Cases that convert to xarray
    if isinstance(obj, xr.Dataset):
        dataset = obj
    elif isinstance(obj, dict):
        dataset = dict_to_dataset(obj, coords=coords, dims=dims)
    elif isinstance(obj, np.ndarray):
        dataset = dict_to_dataset({'x': obj}, coords=coords, dims=dims)
    else:
        allowable_types = (
            'xarray dataset',
            'dict',
            'netcdf file',
            'numpy array',
            'pystan fit',
            'pymc3 trace'
        )
        raise ValueError('Can only convert {} to InferenceData, not {}'.format(
            ', '.join(allowable_types), obj.__class__.__name__))

    return InferenceData(**{group: dataset})


def convert_to_dataset(obj, *_, group='posterior', coords=None, dims=None):
    """Convert a supported object to an xarray dataset

    This function is idempotent, in that it will return xarray.Dataset functions
    unchanged. Raises `ValueError` if the desired group can not be extracted.

    Note this goes through a DataInference object. See `convert_to_inference_data`
    for more details. Raises ValueError if it can not work out the desired
    conversion.

    Parameters
    ----------
    obj : dict, str, np.ndarray, xr.Dataset, pystan fit, pymc3 trace
        A supported object to convert to InferenceData:
            InferenceData: returns unchanged
            str: Attempts to load the netcdf dataset from disk
            pystan fit: Automatically extracts data
            pymc3 trace: Automatically extracts data
            xarray.Dataset: adds to InferenceData as only group
            dict: creates an xarray dataset as the only group
            numpy array: creates an xarray dataset as the only group, gives the
                         array an arbitrary name
    group : str
        If `obj` is a dict or numpy array, assigns the resulting xarray
        dataset to this group.
    coords : dict[str, iterable]
        A dictionary containing the values that are used as index. The key
        is the name of the dimension, the values are the index values.
    dims : dict[str, List(str)]
        A mapping from variables to a list of coordinate names for the variable

    Returns
    -------
    xarray.Dataset
    """
    inference_data = convert_to_inference_data(obj, group=group, coords=coords, dims=dims)
    dataset = getattr(inference_data, group, None)
    if dataset is None:
        raise ValueError('Can not extract {group}! See {filename} for other '
                         'conversion utilities.'.format(group=group, filename='__file__'))
    return dataset


class requires: # pylint: disable=invalid-name
    """Decorator to return None if an object does not have the required attribute"""
    def __init__(self, *props):
        self.props = props

    def __call__(self, func):
        def wrapped(cls, *args, **kwargs):
            for prop in self.props:
                if getattr(cls, prop) is None:
                    return None
            return func(cls, *args, **kwargs)
        return wrapped


def numpy_to_data_array(ary, *_, var_name='data', coords=None, dims=None):
    default_dims = ['chain', 'draw']
    if dims is None:
        can_squeeze = False
        if len(ary.shape) < 3:
            ary = np.atleast_3d(ary)
            can_squeeze = True # added a dimension, might remove it too
        n_chains, n_samples, *shape = ary.shape
        if n_chains > n_samples:
            warnings.warn('More chains ({n_chains}) than draws ({n_samples}). '
                          'Passed array should have shape (chains, draws, *shape)'.format(
                              n_chains=n_chains, n_samples=n_samples), SyntaxWarning)

        coords = {}
        dims = default_dims
        if can_squeeze and len(shape) == 1 and shape[0] == 1:
            ary = np.squeeze(ary, axis=-1)
        else:
            for idx, dim_len in enumerate(shape):
                dims.append('{var_name}_dim_{idx}'.format(var_name=var_name, idx=idx))
                coords[dims[-1]] = np.arange(dim_len)
    else:
        dims = list(dims)
        coords = dict(coords)
        if dims[:2] != default_dims:
            dims = default_dims + dims
    n_chains, n_samples, *_ = ary.shape
    if 'chain' not in coords:
        coords['chain'] = np.arange(n_chains)
    if 'draw' not in coords:
        coords['draw'] = np.arange(n_samples)

    coords = {key: xr.IndexVariable((key,), data=coords[key]) for key in dims}
    return xr.DataArray(ary, coords=coords, dims=dims)


def dict_to_dataset(data, *_, coords=None, dims=None):
    if dims is None:
        dims = {}

    data_vars = {}
    for key, values in data.items():
        data_vars[key] = numpy_to_data_array(values,
                                             var_name=key,
                                             coords=coords,
                                             dims=dims.get(key))
    return xr.Dataset(data_vars=data_vars)


class PyMC3Converter:
    def __init__(self, *_, trace=None, prior=None, posterior_predictive=None,
                 coords=None, dims=None):
        self.trace = trace
        self.prior = prior
        self.posterior_predictive = posterior_predictive
        self.coords = coords
        self.dims = dims

    @requires('trace')
    def posterior_to_xarray(self):
        """Convert the posterior to an xarray dataset
        """
        var_names = pm.utils.get_default_varnames(self.trace.varnames, include_transformed=False)
        data = {}
        for var_name in var_names:
            data[var_name] = np.array(self.trace.get_values(var_name, combine=False))
        return dict_to_dataset(data, coords=self.coords, dims=self.dims)

    @requires('trace')
    def sample_stats_to_xarray(self):
        data = {}
        for stat in self.trace.stat_names:
            data[stat] = np.array(self.trace.get_sampler_stats(stat, combine=False))
        return dict_to_dataset(data)

    @requires('posterior_predictive')
    def posterior_predictive_to_xarray(self):
        data = {k: np.expand_dims(v, 0) for k, v in self.posterior_predictive.items()}
        return dict_to_dataset(data, coords=self.coords, dims=self.dims)

    @requires('prior')
    def prior_to_xarray(self):
        return dict_to_dataset({k: np.expand_dims(v, 0) for k, v in self.prior.items()},
                               coords=self.coords,
                               dims=self.dims)

    def to_inference_data(self):
        return InferenceData(**{
            'posterior': self.posterior_to_xarray(),
            'sample_stats': self.sample_stats_to_xarray(),
            'posterior_predictive': self.posterior_predictive_to_xarray(),
            'prior': self.prior_to_xarray(),
        })


class PyStanConverter:
    def __init__(self, *_, fit=None, coords=None, dims=None):
        self.fit = fit
        self.coords = coords
        self.dims = dims
        self._var_names = fit.model_pars

    @requires('fit')
    def posterior_to_xarray(self):
        dtypes = self.infer_dtypes()
        data = {}
        var_dict = self.fit.extract(self._var_names, dtypes=dtypes, permuted=False)
        for var_name, values in var_dict.items():
            data[var_name] = np.swapaxes(values, 0, 1)
        return dict_to_dataset(data, coords=self.coords, dims=self.dims)

    @requires('fit')
    def sample_stats_to_xarray(self):
        dtypes = {
            'divergent__' : bool,
            'n_leapfrog__' : np.int64,
            'treedepth__' : np.int64,
        }

        rename_key = {
            'accept_stat__' : 'accept_stat',
            'divergent__' : 'diverging',
            'energy__' : 'energy',
            'lp__' : 'lp',
            'n_leapfrog__' : 'n_leapfrog',
            'stepsize__' : 'stepsize',
            'treedepth__' : 'treedepth',
        }

        sampler_params = self.fit.get_sampler_params(inc_warmup=False)
        data = {}
        for key in sampler_params[0]:
            name = rename_key.get(key, re.sub('__$', "", key))
            data[name] = np.vstack([j[key].astype(dtypes.get(key)) for j in sampler_params])
        return dict_to_dataset(data, coords=self.coords, dims=self.dims)

    def infer_dtypes(self):
        pattern_remove_comments = re.compile(
            r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
            re.DOTALL|re.MULTILINE
        )
        stan_integer = r"int"
        stan_limits = r"(?:\<[^\>]+\>)*" # ignore group: 0 or more <....>
        stan_param = r"([^;=\s\[]+)" # capture group: ends= ";", "=", "[" or whitespace
        stan_ws = r"\s*" # 0 or more whitespace
        pattern_int = re.compile(
            "".join((stan_integer, stan_ws, stan_limits, stan_ws, stan_param)),
            re.IGNORECASE
        )
        stan_code = self.fit.get_stancode()
        # remove deprecated comments
        stan_code = "\n".join(\
                line if "#" not in line else line[:line.find("#")]\
                for line in stan_code.splitlines())
        stan_code = re.sub(pattern_remove_comments, "", stan_code)
        stan_code = stan_code.split("generated quantities")[-1]
        dtypes = re.findall(pattern_int, stan_code)
        dtypes = {item.strip() : 'int' for item in dtypes if item.strip() in self._var_names}
        return dtypes

    def to_inference_data(self):
        return InferenceData(**{
            'posterior': self.posterior_to_xarray(),
            'sample_stats': self.sample_stats_to_xarray(),
        })


def pymc3_to_inference_data(*_, trace=None, prior=None, posterior_predictive=None,
                            coords=None, dims=None):
    return PyMC3Converter(
        trace=trace,
        prior=prior,
        posterior_predictive=posterior_predictive,
        coords=coords,
        dims=dims).to_inference_data()


def pystan_to_inference_data(*_, fit=None, coords=None, dims=None):
    return PyStanConverter(
        fit=fit,
        coords=coords,
        dims=dims).to_inference_data()
