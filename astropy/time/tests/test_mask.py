# Licensed under a 3-clause BSD style license - see LICENSE.rst

import functools

import numpy as np
import pytest

from astropy import units as u
from astropy.table import Table
from astropy.time import Time
from astropy.utils import iers
from astropy.utils.compat import NUMPY_LT_1_26, PYTHON_LT_3_11
from astropy.utils.compat.optional_deps import HAS_H5PY
from astropy.utils.masked import Masked

allclose_sec = functools.partial(
    np.allclose, rtol=2.0**-52, atol=2.0**-52 * 24 * 3600
)  # 20 ps atol
is_masked = np.ma.is_masked

# The first form is expanded to r"can't set attribute '{0}'" in Python 3.10, and replaced
# with the more informative second form as of 3.11 (python/cpython#31311).
no_setter_err = (
    r"can't set attribute"
    if PYTHON_LT_3_11
    else r"property '{0}' of '{1}' object has no setter"
)


def test_simple():
    t = Time([1, 2, 3], format="cxcsec")
    assert t.masked is False
    assert np.all(t.mask == [False, False, False])

    # Before masking, format output does not have a mask
    # (it is an ndarray like always)
    assert not hasattr(t.value, "mask")
    assert not hasattr(t.unix, "mask")

    t[2] = np.ma.masked
    assert t.masked is True
    assert np.all(t.mask == [False, False, True])
    assert allclose_sec(t.value[:2], [1, 2])
    assert is_masked(t.value[2])
    assert is_masked(t[2].value)

    # After masking format output has a mask.
    assert hasattr(t.value, "mask")
    assert hasattr(t.unix, "mask")

    # Can also unmask.
    t[2] = np.ma.nomask
    assert np.all(t.mask == [False, False, False])
    # Which means one can become unmasked again.
    assert not t.masked
    assert not hasattr(t.value, "mask")
    assert not hasattr(t.unix, "mask")
    # Combo just for completeness
    t[1:] = np.ma.masked
    t[1] = np.ma.nomask
    assert np.all(t.mask == [False, False, True])
    assert t.masked


def test_scalar_init():
    t = Time("2000:001")
    assert t.masked is False
    assert t.mask == np.array(False)


def test_mask_not_writeable():
    t = Time("2000:001")
    with pytest.raises(
        AttributeError, match=no_setter_err.format("mask", t.__class__.__name__)
    ):
        t.mask = True

    t = Time(["2000:001"])
    with pytest.raises(ValueError, match="assignment destination is read-only"):
        t.mask[0] = True

    # But we can set it to masked directly.
    t[0] = np.ma.masked
    assert np.all(t.mask == [True])
    # After this, the mask should again not be writeable.
    with pytest.raises(ValueError, match="assignment destination is read-only"):
        t.mask[0] = False

    # Should also not be writeable if we initialize with masked elements.
    t = Time(Masked(["2000:001", "2000:002"], mask=[False, True]))
    with pytest.raises(ValueError, match="assignment destination is read-only"):
        t.mask[0] = True
    # But again we can set it to masked directly.
    t[0] = np.ma.masked
    assert np.all(t.mask == [True, True])
    # Check that the mask remains shared.
    assert np.may_share_memory(t._time.jd1.mask, t._time.jd2.mask)


def test_str():
    t = Time(["2000:001", "2000:002"])
    t[1] = np.ma.masked
    assert str(t) == "['2000:001:00:00:00.000'                     ———]"
    assert (
        repr(t)
        == "<Time object: scale='utc' format='yday' value=['2000:001:00:00:00.000'                     ———]>"
    )

    expected = [
        "MaskedNDArray(['2000-01-01 00:00:00.000',                       ———],",
        "              dtype='<U23')",
    ]

    # Note that we need to take care to allow for big-endian platforms,
    # for which the dtype will be >U23 instead of <U23, which we do with
    # the call to replace().
    assert repr(t.iso).replace(">U23", "<U23").splitlines() == expected

    # Assign value to unmask
    t[1] = "2000:111"
    assert str(t) == "['2000:001:00:00:00.000' '2000:111:00:00:00.000']"
    assert t.masked is False


def test_transform():
    with iers.conf.set_temp("auto_download", False):
        t = Time(["2000:001", "2000:002"])
        t[1] = np.ma.masked

        # Change scale (this tests the ERFA machinery with masking as well)
        t_ut1 = t.ut1
        assert is_masked(t_ut1.value[1])
        assert not is_masked(t_ut1.value[0])
        assert np.all(t_ut1.mask == [False, True])
        # Check the mask is a copy, so we won't back-propagate changes.
        assert not np.may_share_memory(t_ut1.mask, t.mask)

        # Change format
        t_unix = t.unix
        assert is_masked(t_unix[1])
        assert not is_masked(t_unix[0])
        assert np.all(t_unix.mask == [False, True])
        # Check the mask is a copy.
        assert not np.may_share_memory(t_unix.mask, t.mask)


def test_masked_input():
    v0 = np.ma.MaskedArray([[1, 2], [3, 4]])  # No masked elements
    v1 = np.ma.MaskedArray([[1, 2], [3, 4]], mask=[[True, False], [False, False]])
    v2 = np.ma.MaskedArray([[10, 20], [30, 40]], mask=[[False, False], [False, True]])

    # Init from various combinations of masked arrays
    t = Time(v0, format="cxcsec")
    assert np.ma.allclose(t.value, v0)
    assert np.all(t.mask == [[False, False], [False, False]])
    assert t.masked is False

    t = Time(v1, format="cxcsec")
    assert np.ma.allclose(t.value, v1)
    assert np.all(t.mask == v1.mask)
    value = t.value
    assert np.all(value.mask == v1.mask)
    assert t.masked is True
    # Check that masked are not shared with input or output.
    assert not np.may_share_memory(t.mask, v1.mask)
    assert not np.may_share_memory(value.mask, t.mask)
    # But they should be shared in the private _jd1, _jd2.
    assert np.may_share_memory(t._time.jd1.mask, t._time.jd2.mask)

    t = Time(v1, v2, format="cxcsec")
    assert np.ma.allclose(t.value, v1 + v2)
    assert np.all(t.mask == (v1 + v2).mask)
    assert t.masked is True

    t = Time(v0, v1, format="cxcsec")
    assert np.ma.allclose(t.value, v0 + v1)
    assert np.all(t.mask == (v0 + v1).mask)
    assert t.masked is True

    t = Time(0, v2, format="cxcsec")
    assert np.ma.allclose(t.value, v2)
    assert np.all(t.mask == v2.mask)
    assert t.masked is True

    # Init from a string masked array
    t_iso = t.iso
    t2 = Time(t_iso)
    assert np.all(t2.value == t_iso)
    assert np.all(t2.mask == v2.mask)
    assert t2.masked is True


@pytest.mark.parametrize("masked_cls", [np.ma.MaskedArray, Masked])
@pytest.mark.parametrize("val", [0, np.nan, [0], [np.nan]])
def test_all_masked_input(masked_cls, val):
    """Fix for #9612"""
    # Test with jd=0 and jd=np.nan. Both triggered an exception prior to #9624
    # due to astropy.utils.exceptions.ErfaError.
    val = masked_cls(val, mask=True)
    t = Time(val, format="jd")
    if val.ndim:
        assert str(t.iso).endswith("———]")
    else:
        assert str(t.iso).endswith("———")


def test_serialize_fits_masked(tmp_path):
    tm = Time([1, 2, 3], format="cxcsec")
    tm[1] = np.ma.masked

    fn = tmp_path / "tempfile.fits"
    t = Table([tm])
    t.write(fn)

    t2 = Table.read(fn, astropy_native=True)

    # Time FITS handling does not current round-trip format in FITS
    t2["col0"].format = tm.format

    assert t2["col0"].masked
    assert np.all(t2["col0"].mask == [False, True, False])
    assert np.all(t2["col0"].value == t["col0"].value)


@pytest.mark.skipif(not HAS_H5PY, reason="Needs h5py")
def test_serialize_hdf5_masked(tmp_path):
    tm = Time([1, 2, 3], format="cxcsec")
    tm[1] = np.ma.masked

    fn = tmp_path / "tempfile.hdf5"
    t = Table([tm])
    t.write(fn, path="root", serialize_meta=True)
    t2 = Table.read(fn)

    assert t2["col0"].masked
    assert np.all(t2["col0"].mask == [False, True, False])
    assert np.all(t2["col0"].value == t["col0"].value)


# Ignore warning in MIPS https://github.com/astropy/astropy/issues/9750
@pytest.mark.filterwarnings("ignore:invalid value encountered")
@pytest.mark.parametrize("serialize_method", ["jd1_jd2", "formatted_value"])
def test_serialize_ecsv_masked(serialize_method, tmp_path):
    tm = Time([1, 2, 3], format="cxcsec")
    tm[1] = np.ma.masked

    tm.info.serialize_method["ecsv"] = serialize_method

    fn = tmp_path / "tempfile.ecsv"
    t = Table([tm])
    t.write(fn)
    t2 = Table.read(fn)

    assert t2["col0"].masked
    assert np.all(t2["col0"].mask == [False, True, False])
    # Serializing formatted_value loses some precision.
    atol = 0.1 * u.us if serialize_method == "formatted_value" else 1 * u.ps
    assert np.all(abs(t2["col0"] - t["col0"]) <= atol)


@pytest.mark.parametrize("format_", Time.FORMATS)
@pytest.mark.parametrize("masked_cls", [np.ma.MaskedArray, Masked])
def test_all_formats(format_, masked_cls):
    mjd = np.array([55000.25, 55000.375, 55001.125])
    mask = np.array([True, False, False])
    mjdm = masked_cls(mjd, mask=mask)
    assert np.may_share_memory(mjdm.mask, mask)
    t = Time(mjd, format="mjd")
    tm = Time(mjdm, format="mjd")
    assert tm.masked and np.all(tm.mask == mask)
    assert not np.may_share_memory(tm.mask, mask)

    # Get values in the given format, check that these are always Masked and
    # that they are correct (ignoring masked ones, which get adjusted on Time
    # initialization, in core._check_for_masked_and_fill).
    t_format = getattr(t, format_)
    tm_format = getattr(tm, format_)
    assert isinstance(tm_format, Masked)
    assert np.all(tm_format == t_format)
    # While we are at it, check that the mask is not shared.
    assert not np.may_share_memory(tm_format.mask, tm.mask)

    # Verify that we can also initialize with the format and that this gives
    # the right result and mask too.
    t2 = Time(t_format, format=format_)
    tm2 = Time(tm_format, format=format_)
    assert tm2.masked and np.all(tm2.mask == mask)
    assert np.all(tm2 == t2)
