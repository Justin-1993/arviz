import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import mode
from .kdeplot import kdeplot, fast_kde
from ..stats import hpd
from ..utils import convert_to_xarray
from .plot_utils import xarray_var_iter, _scale_text, make_label, default_grid


def posteriorplot(data, var_names=None, coords=None, figsize=None, textsize=None, alpha=0.05,
                  round_to=1, point_estimate='mean', rope=None, ref_val=None, kind='kde', bw=4.5,
                  bins=None, **kwargs):
    """
    Plot Posterior densities in the style of John K. Kruschke's book.

    Parameters
    ----------
    data : xarray, or object that can be converted (pystan or pymc3 draws)
        Posterior samples
    var_names : list of variable names
        Variables to be plotted, two variables are required.
    coords : mapping, optional
        Coordinates of var_names to be plotted. Passed to `Dataset.sel`
    figsize : tuple
        Figure size. If None, size is (12, num of variables * 2)
    textsize: int
        Text size of the point_estimates, axis ticks, and HPD. If None it will be autoscaled
        based on figsize.
    alpha : float, optional
        Alpha value for (1-alpha)*100% credible intervals. Defaults to 0.05.
    round_to : int
        Controls formatting for floating point numbers
    point_estimate: str
        Must be in ('mode', 'mean', 'median')
    rope: tuple or dictionary of tuples
        Lower and upper values of the Region Of Practical Equivalence. If a list is provided, its
        length should match the number of variables.
    ref_val: float or dictionary of floats
        display the percentage below and above the values in ref_val. If a list is provided, its
        length should match the number of variables.
    kind: str
        Type of plot to display (kde or hist) For discrete variables this argument is ignored and
        a histogram is always used.
    bw : float
        Bandwidth scaling factor for the KDE. Should be larger than 0. The higher this number the
        smoother the KDE will be. Defaults to 4.5 which is essentially the same as the Scott's rule
        of thumb (the default rule used by SciPy). Only works if `kind == kde`.
    bins : integer or sequence or 'auto', optional
        Controls the number of bins, accepts the same keywords `matplotlib.hist()` does. Only works
        if `kind == hist`. If None (default) it will use `auto` for continuous variables and
        `range(xmin, xmax + 1)` for discrete variables.
    **kwargs
        Passed as-is to plt.hist() or plt.plot() function depending on the value of `kind`.

    Returns
    -------
    ax : matplotlib axes

    Examples
    --------
    Show one example plot for reference

    .. plot::
        :context: close-figs

        >>> import arviz as az
        >>> non_centered = az.load_arviz_data('non_centered_eight')
        >>> az.posteriorplot(non_centered, var_names=('mu', 'tau'), textsize=12)

    Change a parameter and show another plot to show what happens

    .. plot::
        :context: close-figs

        >>> import arviz as az
        >>> non_centered = az.load_arviz_data('non_centered_eight')
        >>> az.posteriorplot(non_centered, var_names=('theta_tilde',),
                             ref_val=0, rope=(-1, 1), textsize=11)

    """
    data = convert_to_xarray(data)

    if coords is None:
        coords = {}


    plotters = list(xarray_var_iter(data, var_names=var_names, combined=True))
    rows, cols = default_grid(len(plotters))

    if figsize is None:
        figsize = (3.5 * cols, 3 * rows)

    textsize, linewidth, _ = _scale_text(figsize, textsize, scale_ratio=0.9)

    _, axes = plt.subplots(rows, cols, figsize=figsize, squeeze=False)

    for (var_name, selection, x), ax in zip(plotters, axes.flatten()):
        _plot_posterior_op(x.flatten(), var_name, selection, ax=ax, bw=bw, linewidth=linewidth,
                           bins=bins, kind=kind, point_estimate=point_estimate,
                           round_to=round_to, alpha=alpha, ref_val=ref_val, rope=rope,
                           textsize=textsize, **kwargs)

        ax.set_title(make_label(var_name, selection), fontsize=textsize)
    return axes



def _plot_posterior_op(values, var_name, selection, ax, bw, linewidth, bins, kind,
                       point_estimate, round_to, alpha, ref_val, rope, textsize, **kwargs):
    """
    Artist to draw posterior.
    """
    def format_as_percent(x, round_to=0):
        return '{0:.{1:d}f}%'.format(100 * x, round_to)

    def display_ref_val():
        if ref_val is None:
            return
        elif isinstance(ref_val, dict):
            for sel in ref_val.get(var_name, []):
                if all(k in selection and selection[k] == v for k, v in sel.items()):
                    val = sel['ref_val']
                    break
        elif np.isscalar(ref_val):
            val = ref_val
        else:
            raise ValueError('Argument `ref_val` must be None, a constant, or a '
                             'dictionary like {"var_name": {"ref_val": (lo, hi)}}')


        less_than_ref_probability = (values < val).mean()
        greater_than_ref_probability = (values >= val).mean()
        ref_in_posterior = "{} <{:g}< {}".format(format_as_percent(less_than_ref_probability, 1),
                                                 val,
                                                 format_as_percent(greater_than_ref_probability, 1))
        ax.axvline(val, ymin=0.05, ymax=.75, color='C1', lw=linewidth, alpha=0.65)
        ax.text(values.mean(), plot_height * 0.6, ref_in_posterior, size=textsize,
                color='C1', weight='semibold', horizontalalignment='center')

    def display_rope():
        if rope is None:
            return
        elif isinstance(rope, dict):
            vals = None
            for sel in rope.get(var_name, []):
                if all(k in selection and selection[k] == v for k, v in sel.items() if k != 'rope'):
                    vals = sel['rope']
                    break
            if vals is None:
                return
        elif len(rope) == 2:
            vals = rope
        else:
            raise ValueError('Argument `rope` must be None, a dictionary like'
                             '{"var_name": {"rope": (lo, hi)}}, or an'
                             'iterable of length 2')

        ax.plot(vals, (plot_height * 0.02, plot_height * 0.02), lw=linewidth*5, color='C2',
                solid_capstyle='round', zorder=0, alpha=0.7)
        text_props = {'size': textsize, 'horizontalalignment': 'center', 'color': 'C2'}
        ax.text(vals[0], plot_height * 0.2, vals[0], weight='semibold', **text_props)
        ax.text(vals[1], plot_height * 0.2, vals[1], weight='semibold', **text_props)

    def display_point_estimate():
        if not point_estimate:
            return
        if point_estimate not in ('mode', 'mean', 'median'):
            raise ValueError(
                "Point Estimate should be in ('mode','mean','median')")
        if point_estimate == 'mean':
            point_value = values.mean()
        elif point_estimate == 'mode':
            if isinstance(values.iloc[0], float):
                density, lower, upper = fast_kde(values, bw=bw)
                x = np.linspace(lower, upper, len(density))
                point_value = x[np.argmax(density)]
            else:
                point_value = mode(values.round(round_to))[0][0]
        elif point_estimate == 'median':
            point_value = np.median(values)
        point_text = '{}={:.{}f}'.format(point_estimate, point_value, round_to)

        ax.text(point_value, plot_height * 0.8, point_text, size=textsize,
                horizontalalignment='center')

    def display_hpd():
        hpd_intervals = hpd(values, alpha=alpha)
        ax.plot(hpd_intervals, (plot_height * 0.02, plot_height * 0.02), lw=linewidth*2, color='k',
                solid_capstyle='round')
        ax.text(hpd_intervals[0], plot_height * 0.07,
                hpd_intervals[0].round(round_to),
                size=textsize, horizontalalignment='center')
        ax.text(hpd_intervals[1], plot_height * 0.07,
                hpd_intervals[1].round(round_to),
                size=textsize, horizontalalignment='center')
        ax.text((hpd_intervals[0] + hpd_intervals[1]) / 2, plot_height * 0.3,
                format_as_percent(1 - alpha) + ' HPD',
                size=textsize, horizontalalignment='center')

    def format_axes():
        ax.yaxis.set_ticks([])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_visible(False)
        ax.spines['bottom'].set_visible(True)
        ax.xaxis.set_ticks_position('bottom')
        ax.tick_params(axis='x', direction='out', width=1, length=3,
                       color='0.5', labelsize=textsize)
        ax.spines['bottom'].set_color('0.5')

    if kind == 'kde' and values.dtype.kind == 'f':
        kdeplot(values,
                bw=bw,
                fill_kwargs={'alpha': kwargs.pop('fill_alpha', 0)},
                plot_kwargs={'linewidth': linewidth},
                ax=ax)

    else:
        if bins is None:
            if values.dtype.kind == 'i':
                xmin = values.min()
                xmax = values.max()
                bins = range(xmin, xmax + 2)
                ax.set_xlim(xmin - 0.5, xmax + 0.5)
            else:
                bins = 'auto'
        kwargs.setdefault('align', 'left')
        kwargs.setdefault('color', 'C0')
        ax.hist(values, bins=bins, alpha=0.35, **kwargs)

    plot_height = ax.get_ylim()[1]

    format_axes()
    display_hpd()
    display_point_estimate()
    if ref_val is not None:
        display_ref_val()
    if rope is not None:
        display_rope()
