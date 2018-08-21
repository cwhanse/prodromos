#from generic_vpp_server import GenericVPPServer
#from multiprocessing import Manager

import pvlib
import pytz
from datetime import datetime
from datetime import timedelta
import pandas as pd
import numpy as np
import statsmodels.api as sm
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

class PVobj():
    # create an instance of a PV class object
    def __init__(self, derid, lat, lon, alt, tz,
                 tilt, azimuth, dc_capacity, ac_capacity,
                 forecast_method='ARMA', surrogateid=None,
                 base_year=2016):
        self.derid = derid
        # TODO: using a surrogate in forecasting is NOT implemented
        self.surrogateid = surrogateid
        if forecast_method.lower() in ['arma', 'persistence']:
            self.forecast_method = forecast_method.lower()
        else:
            # TODO: raise error
            warnstring = 'DER ID ' + str(derid) + \
               ' : no forecast method specified, default to persistence'
            raise RuntimeWarning(warnstring)
            self.forecast_method = None
        self.dc_capacity = dc_capacity
        self.ac_capacity = ac_capacity
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.timezone = tz
        self.tilt = tilt
        self.azimuth = azimuth

        # pre-compute clear sky power
        # use datetime index for an actual year
        dr = pd.DatetimeIndex(start=datetime(base_year, 1, 1, 0, 0, 0, tzinfo=tz),
                              end=datetime(base_year, 12, 31, 23, 59, 0, tzinfo=tz),
                              freq='1T')

        self.clearsky = pd.DataFrame(index=dr, columns=['csGHI',
                                                        'csPOA',
                                                        'dc_power',
                                                        'ac_power'])
        self.base_time = datetime(base_year, 1, 1, 0, 0, 0, tzinfo=tz)

        clearSky = clear_sky_model(self, dr)
        self.clearsky['csGHI'] = clearSky['ghi']
        self.clearsky['csPOA'] = clearSky['poa']
        self.clearsky['dc_power'] = self.clearsky['csPOA'] / 1000 * self.dc_capacity
        self.clearsky['ac_power'] = np.where(
                self.clearsky['dc_power']>self.ac_capacity,
                self.ac_capacity,
                self.clearsky['dc_power']
                )

        # add time of year in seconds
        self.clearsky['time_of_year'] = convert_to_time_of_year(dr)

    # PVobj member functions
    def forecast(self,
                 start,
                 end,
                 history,
                 deltat=timedelta(minutes=15),
                 dataWindowLength=timedelta(hours=1),
                 order=(1, 1, 0)):

        # wrapper for functions forecast_ARMA and forecast_persistence

        if self.forecast_method=='arma':
            return forecast_ARMA(self,
                                 start,
                                 end,
                                 history,
                                 deltat,
                                 dataWindowLength,
                                 order)

        elif self.forecast_method=='persistence':
            return forecast_persistence(self,
                                        start,
                                        end,
                                        history,
                                        deltat,
                                        dataWindowLength)


# Forecast functions
def solar_position(pvobj, dr):
    # returns ephemeris in a dataframe sp

    Location = pvlib.location.Location(pvobj.lat,
                                       pvobj.lon,
                                       pvobj.timezone,
                                       pvobj.alt)

    sp = ephemeris(dr, Location.latitude, Location.longitude)

    return sp


def ephemeris(time, latitude, longitude, pressure=101325, temperature=12):
    """
    Python-native solar position calculator.  Included here from pvlib until
    performance improvement is made in pvlib source.

    Parameters
    ----------
    time : pandas.DatetimeIndex
    latitude : float
    longitude : float
    pressure : float or Series, default 101325
        Ambient pressure (Pascals)
    temperature : float or Series, default 12
        Ambient temperature (C)

    Returns
    -------

    DataFrame with the following columns:

        * apparent_elevation : apparent sun elevation accounting for
          atmospheric refraction.
        * elevation : actual elevation (not accounting for refraction)
          of the sun in decimal degrees, 0 = on horizon.
          The complement of the zenith angle.
        * azimuth : Azimuth of the sun in decimal degrees East of North.
          This is the complement of the apparent zenith angle.
        * apparent_zenith : apparent sun zenith accounting for atmospheric
          refraction.
        * zenith : Solar zenith angle
        * solar_time : Solar time in decimal hours (solar noon is 12.00).

    References
    -----------

    Grover Hughes' class and related class materials on Engineering
    Astronomy at Sandia National Laboratories, 1985.

    See also
    --------
    pyephem, spa_c, spa_python

    """

    # Added by Rob Andrews (@Calama-Consulting), Calama Consulting, 2014
    # Edited by Will Holmgren (@wholmgren), University of Arizona, 2014

    # Most comments in this function are from PVLIB_MATLAB or from
    # pvlib-python's attempt to understand and fix problems with the
    # algorithm. The comments are *not* based on the reference material.
    # This helps a little bit:
    # http://www.cv.nrao.edu/~rfisher/Ephemerides/times.html

    # the inversion of longitude is due to the fact that this code was
    # originally written for the convention that positive longitude were for
    # locations west of the prime meridian. However, the correct convention (as
    # of 2009) is to use negative longitudes for locations west of the prime
    # meridian. Therefore, the user should input longitude values under the
    # correct convention (e.g. Albuquerque is at -106 longitude), but it needs
    # to be inverted for use in the code.

    Latitude = latitude
    Longitude = -1 * longitude

    Abber = 20 / 3600.
    LatR = np.radians(Latitude)

    # the SPA algorithm needs time to be expressed in terms of
    # decimal UTC hours of the day of the year.

    # if localized, convert to UTC. otherwise, assume UTC.
    try:
        time_utc = time.tz_convert('UTC')
    except TypeError:
        time_utc = time

    # strip out the day of the year and calculate the decimal hour
    DayOfYear = time_utc.dayofyear
    DecHours = (time_utc.hour + time_utc.minute/60. + time_utc.second/3600. +
                time_utc.microsecond/3600.e6)

    # np.array needed for pandas > 0.20
    UnivDate = np.array(DayOfYear)
    UnivHr = np.array(DecHours)

    Yr = np.array(time_utc.year) - 1900
    YrBegin = 365 * Yr + np.floor((Yr - 1) / 4.) - 0.5

    Ezero = YrBegin + UnivDate
    T = Ezero / 36525.

    # Calculate Greenwich Mean Sidereal Time (GMST)
    GMST0 = 6 / 24. + 38 / 1440. + (
        45.836 + 8640184.542 * T + 0.0929 * T ** 2) / 86400.
    GMST0 = 360 * (GMST0 - np.floor(GMST0))
    GMSTi = np.mod(GMST0 + 360 * (1.0027379093 * UnivHr / 24.), 360)

    # Local apparent sidereal time
    LocAST = np.mod((360 + GMSTi - Longitude), 360)

    EpochDate = Ezero + UnivHr / 24.
    T1 = EpochDate / 36525.

    ObliquityR = np.radians(
        23.452294 - 0.0130125 * T1 - 1.64e-06 * T1 ** 2 + 5.03e-07 * T1 ** 3)
    MlPerigee = 281.22083 + 4.70684e-05 * EpochDate + 0.000453 * T1 ** 2 + (
        3e-06 * T1 ** 3)
    MeanAnom = np.mod((358.47583 + 0.985600267 * EpochDate - 0.00015 *
                       T1 ** 2 - 3e-06 * T1 ** 3), 360)
    Eccen = 0.01675104 - 4.18e-05 * T1 - 1.26e-07 * T1 ** 2
    EccenAnom = MeanAnom
    E = 0

    while np.max(abs(EccenAnom - E)) > 0.0001:
        E = EccenAnom
        EccenAnom = MeanAnom + np.degrees(Eccen)*np.sin(np.radians(E))

    TrueAnom = (
        2 * np.mod(np.degrees(np.arctan2(((1 + Eccen) / (1 - Eccen)) ** 0.5 *
                   np.tan(np.radians(EccenAnom) / 2.), 1)), 360))
    EcLon = np.mod(MlPerigee + TrueAnom, 360) - Abber
    EcLonR = np.radians(EcLon)
    DecR = np.arcsin(np.sin(ObliquityR)*np.sin(EcLonR))

    RtAscen = np.degrees(np.arctan2(np.cos(ObliquityR)*np.sin(EcLonR),
                                    np.cos(EcLonR)))

    HrAngle = LocAST - RtAscen
    HrAngleR = np.radians(HrAngle)
    HrAngle = HrAngle - (360 * ((abs(HrAngle) > 180)))

    SunAz = np.degrees(np.arctan2(-np.sin(HrAngleR),
                                  np.cos(LatR)*np.tan(DecR) -
                                  np.sin(LatR)*np.cos(HrAngleR)))
    SunAz[SunAz < 0] += 360

    SunEl = np.degrees(np.arcsin(
        np.cos(LatR) * np.cos(DecR) * np.cos(HrAngleR) +
        np.sin(LatR) * np.sin(DecR)))

    SolarTime = (180 + HrAngle) / 15.

    # Calculate refraction correction
    Elevation = SunEl
    TanEl = pd.Series(np.tan(np.radians(Elevation)), index=time_utc)
    Refract = pd.Series(0, index=time_utc)

    Refract[(Elevation > 5) & (Elevation <= 85)] = (
        58.1/TanEl - 0.07/(TanEl**3) + 8.6e-05/(TanEl**5))

    Refract[(Elevation > -0.575) & (Elevation <= 5)] = (
        Elevation *
        (-518.2 + Elevation*(103.4 + Elevation*(-12.79 + Elevation*0.711))) +
        1735)

    Refract[(Elevation > -1) & (Elevation <= -0.575)] = -20.774 / TanEl

    Refract *= (283/(273. + temperature)) * (pressure/101325.) / 3600.

    ApparentSunEl = SunEl + Refract

    # make output DataFrame
    DFOut = pd.DataFrame(index=time_utc)
    DFOut['apparent_elevation'] = ApparentSunEl
    DFOut['elevation'] = SunEl
    DFOut['azimuth'] = SunAz
    DFOut['apparent_zenith'] = 90 - ApparentSunEl
    DFOut['zenith'] = 90 - SunEl
    DFOut['solar_time'] = SolarTime

    DFOut.index = time
    
    return DFOut


def dniDiscIrrad(weather):
    """
    Use the DISC model to estimate the DNI from GHI

    Parameters
    -----------
    weather : pandas DataFrame
        contains the following keys:
            ghi: global horizontal irradiance in W/m2
            zenith : solar zenith angle in degrees

    Returns
    --------
    DNI : pandas Series
        direct normal irradiance
    """
    disc = pvlib.irradiance.disc(ghi=weather['ghi'],
                                 zenith=weather['zenith'],
                                 datetime_or_doy=weather.index)
    # NA's show up when the DNI component is zero (or less than zero), fill with 0.
    disc.fillna(0, inplace=True)

    return disc['dni']


def DHIfromGHI(weather):
    """
    Solve for DHI from GHI and DNI
        GHI =  DHI + DNI*sin(solar altitude angle)

    Parameters
    -----------
    weather : pandas DataFrame
        contains the following keys:
            ghi : global horizontal irradiance in W/m2
            dni : direct normal irradiance in W/m2
            elevation : solar elevation in degrees

    Returns
    --------
    DHI : pandas Series
        diffuse horizontal irradiance
    """
    return weather['ghi'] - weather['dni'] * \
             np.sin(weather['elevation'] * (np.pi / 180))


def clear_sky_model(pvobj, dr):
    """
    Calculate clear-sky irradiance (GHI, DHI, DNI and POA) and related
    quantities

    Parameters
    -------------

    pvobj : instance of type PVobj

    dr : pandas DatetimeIndex
        times at which irradiance values are calculated

    Returns
    ----------
    clearSky : pandas Dataframe
        contains ghi, dhi, dni, poa, aoi, extraI, beam, diffuseSky,
        diffuseGround, diffuseTotal
    """

    # initialize clear sky df and fill with information
    clearSky = pd.DataFrame(index=pd.DatetimeIndex(dr))
    # get solar position information
    sp = solar_position(pvobj, dr)
    clearSky['zenith'] = sp['zenith']
    clearSky['elevation'] = sp['elevation']

    clearSky['extraI'] = pvlib.irradiance.extraradiation(dr)

    # calculate GHI using Haurwitz model
    clearSky['ghi'] = pvlib.clearsky.haurwitz(sp['apparent_zenith'])

    clearSky['dni'] = dniDiscIrrad(clearSky)

    clearSky['dhi'] = DHIfromGHI(clearSky)

    clearSky['aoi'] = pvlib.irradiance.aoi(surface_tilt=pvobj.tilt,
                                           surface_azimuth=pvobj.azimuth,
                                           solar_zenith=sp['zenith'],
                                           solar_azimuth=sp['azimuth'])

    # Convert the AOI to radians
    clearSky['aoi'] *= (np.pi/180.0)

    # Calculate the POA irradiance based on the given site information
    clearSky['beam'] = pvlib.irradiance.beam_component(pvobj.tilt,
                                                       pvobj.azimuth,
                                                       sp['zenith'],
                                                       sp['azimuth'],
                                                       clearSky['dni'])

    # Calculate the diffuse radiation from the sky (using Hay and Davies)
    clearSky['diffuseSky'] = pvlib.irradiance.haydavies(pvobj.tilt,
                                                        pvobj.azimuth,
                                                        clearSky['dhi'],
                                                        clearSky['dni'],
                                                        clearSky['extraI'],
                                                        sp['zenith'],
                                                        sp['azimuth'])

    # Calculate the diffuse radiation from the ground in the plane of array
    clearSky['diffuseGround'] = pvlib.irradiance.grounddiffuse(pvobj.tilt,
                                                              clearSky['ghi'])

    # Sum the two diffuse to get total diffuse
    clearSky['diffuseTotal'] = clearSky['diffuseGround'] + \
                                 clearSky['diffuseSky']

    # Sum the diffuse and beam to get the total POA irradicance
    clearSky['poa'] = clearSky['diffuseTotal'] + clearSky['beam']

    return clearSky


def calc_clear_index(meas, ub):
    # calculates clear-sky index of meas relative to ub
    # accepts input as pd.Series or np.ndarray
    # returns the same type as the inputs
    clearIndex = calc_ratio(meas, ub)
    clearIndex[clearIndex == np.inf] = 1.0
    clearIndex[np.isnan(clearIndex)] = 1.0
    clearIndex[clearIndex > 1.0] = 1.0

    return clearIndex


def calc_ratio(X, Y):

    # Compute the ratio X / Y
    # inputs X and Y can be pandas.Series, or np.ndarray
    # returns the same type as the inputs
    np.seterr(divide='ignore')
    ratio = np.true_divide(X, Y)
    np.seterr(divide='raise')
    return ratio


def _get_history_data_for_persistence(start, history, dataWindowLength):

    """
    Returns history data for persistence forecast

    Parameters
    -----------

    pvobj : an instance of type PVobj

    start : datetime
        the time for the first forecast value

    history : pandas DataFrame with key 'ac_power'
        historical values of AC power from which the forecast is made.

    dataWindowLenth : timedelta
        time interval in history to be considered

    Returns
    ----------
    fitdata :
        data from history in period of duration dataWindowLength, prior to the
        minimum of either the forecast start or the last time in history

    """

    # find last time to include. Earliest of either forecast start or the last
    # time in history
    end_data_time = min([max(history.index), start])
    # select data within dataWindowLength
    fitdata = history.loc[(history.index>=end_data_time - dataWindowLength) &
                         (history.index<=end_data_time)]

    return fitdata


def is_leap_year(year):
    """Determine whether a year is a leap year."""

    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def convert_to_time_of_year(dr):
    """
    Returns a pandas Series of time of year (number of seconds)

    Parameters
    -----------

    dr : pandas DatetimeIndex

    Returns
    --------

    time_of_year : pandas Series
        time of year in seconds

    """
    
    # create series of earliest time in each corresponding year
    time_of_year = pd.Series(index=dr, data=dr)
    base_times = pd.to_datetime({'year': time_of_year.dt.year,
                                 'month': 1, 
                                 'day': 1,
                                 'hour': 0,
                                 'minute': 0,
                                 'second': 0})
    base_times = base_times.dt.tz_localize(dr.tz)

    ly = time_of_year.dt.year.apply(is_leap_year)
    sec_per_year = 365*24*60*60
    sec_per_leap_year = 366*24*60*60
    
    denom = np.where(ly, sec_per_leap_year, sec_per_year)

    time_of_year = (dr - base_times).dt.total_seconds() / denom

    return time_of_year


def forecast_persistence(pvobj, start, end, history, deltat,
                         dataWindowLength=timedelta(hours=1)):

    """
    Generate forecast for pvobj from start to end at time resolution deltat
    using the persistence method applied to data in history.

    Parameters
    -----------

    pvobj : an instance of type PVobj

    start : datetime
        the time for the first forecast value

    end : datetime
        the last time to be forecast

    history : pandas Series
        historical values of AC power from which the forecast is made.

    deltat : timedelta
        the time interval for the forecast

    dataWindowLenth : timedelta
        time interval in history to be considered

    Returns
    --------

    """
    # get data for forecast
    fitdata = _get_history_data_for_persistence(start,
                                                history,
                                                dataWindowLength)

    # convert data index from datetime to time of year (seconds)
    toy = convert_to_time_of_year(fitdata.index)

    # get clear-sky power at time of year
    cspower = np.interp(toy,
                        pvobj.clearsky['time_of_year'].values,
                        pvobj.clearsky['ac_power'].values)

    # compute average clear sky power index
    cspower_index = calc_ratio(fitdata, cspower).mean()

    # time index for forecast
    dr = pd.DatetimeIndex(start=start, end=end, freq=deltat)

    # get clear sky power profile for forecast period
    toy_dr = convert_to_time_of_year(dr)
    fcst_cspower = np.interp(toy_dr,
                             pvobj.clearsky['time_of_year'].values,
                             pvobj.clearsky['ac_power'].values)

    # forecast ac_power_index using persistence of clear sky power index
    fcst_power = pd.Series(data=fcst_cspower * cspower_index,
                           index=dr,
                           name='ac_power')

    return fcst_power


def _extend_datetime_index(start, end, deltat, earlier):
    """
    Extends a datetime index from  ``start`` to ``end`` at
    interval ``deltat`` to begin prior to ``earlier`` time.

    Parameters
    -------------
    start : datetime
        the start time for the datetime index

    end : datetime
        the end time for the datetime index

    deltat : timedelta
        the time interval for the datetime index

    earlier : datetime
        earlier time to include in the extended datetime index

    Returns
    ------------
    idr : DateTimeIndex
        start replaced by earlier time, time interval and end time maintained

    """

    if earlier > start:
        raise Exception("Error in _extend_datetime_index: earlier > start")
        return

    # number of intervals with length deltat between start of input
    # datetime index and the earlier time to be included
    num_intervals = int(
              (start - earlier).total_seconds() / deltat.seconds)

    # extend datetime index to include earlier time
    idr = pd.DatetimeIndex(start=(start - num_intervals*deltat),
                           end=end,
                           freq=pd.to_timedelta(deltat))

    return idr


def _align_data_to_forecast(fstart, fend, deltat, history):
    """
    Interpolate history to times that are in phase with forecast

    Parameters
    -----------
    fstart : datetime
        first time for the forecast

    fend : datetime
        end time for the forecast

    deltat : timedelta
        interval for the forecast

    history : pandas Series or DataFrame containing key 'ac_power'
        measurements to use for the forecast

=======

    fend : datetime
        end time for the forecast

    deltat : timedelta
        interval for the forecast

    history : pandas Series or DataFrame containing key 'ac_power'
        measurements to use for the forecast

    Returns
    ---------
    idata : pandas DataFrame
        contains history interpolated to times that are in phase with requested
        forecast times

    idr : pandas DatetimeIndex
        datetime index that includes history and forecast periods and is in
        phase with forecast times

    """

    idr = _extend_datetime_index(fstart, fend, deltat, min(history.index))

    tmpdata = pd.DataFrame(index=idr, data=np.nan, columns=['ac_power'])

    # merge history into empty dataframe that has the index we want
    # use outer join to retain time points in history
    newdata = tmpdata.merge(history.to_frame(),
                            how='outer',
                            on=['ac_power'],
                            left_index=True,
                            right_index=True).tz_convert(tmpdata.index.tz)

    # fill in values on idr timesteps by interpolation
    newdata.interpolate(inplace=True)

    # trim to start at first index in idr (in phase with forecast start),
    # and don't overrun history
    idata = newdata[(newdata.index>=min(idr)) &
                    (newdata.index<=max(history.index))].copy()

    # calculates minutes out of phase with midnight
    base = int((idr[0].replace(hour=0, second=0) -
                idr[0].normalize()).total_seconds() / 60)

    # want time averages in phase with forecast start over specified deltat.
    idata = idata.resample(pd.to_timedelta(deltat),
                           closed='left',
                           label='left',
                           base=base).mean()

    return idata, idr


def _get_data_for_ARMA_forecast(pvobj, start, end, deltat, history,
                              dataWindowLength):

    """
    Returns interpolated history data in phase with forecast start time and
    time interval.

    Parameters
    -----------

    pvobj : an instance of type PVobj

    start : datetime
        the time for the first forecast value

    end : datetime
        the last time to be forecast

    deltat : timedelta
        the time interval for the forecast

    history : pandas DataFrame with key 'ac_power'
        historical values of AC power from which the forecast is made.

    dataWindowLenth : timedelta
        time interval in history to be considered

    Returns
    ----------
    fitdata :
        data from history aligned to be in phase with requested forecast

    fdr : pandas DatetimeIndex
        time index for requested forecast

    steps : integer
        number of time steps in the forecast forecast
    """

    # align history data with forecast start time and interval
    idata, idr = _align_data_to_forecast(start, end, deltat, history)

    # select aligned data within dataWindowLength
    end_data_time = max(idata.index)
    first_data_time = end_data_time - dataWindowLength
    fitdata = idata.loc[(idata.index>=first_data_time) &
                        (idata.index<=end_data_time)]

    # determine number of intervals for forecast. start with first interval
    # after the data used to fit the model. +1 because steps counts intervals
    # we want the interval after the last entry in fdr
    f_intervals = len(idr[idr>max(fitdata.index)]) + 1

    return fitdata, f_intervals


def forecast_ARMA(pvobj, start, end, history, deltat,
                  dataWindowLength=timedelta(hours=1), order=None):

    """
    Generate forecast from start to end at time resolution deltat
    using an ARMA model of order fit to AC power data in history.

    Parameters
    -----------

    pvobj : an instance of type PVobj

    start : datetime
        the time for the first forecast value

    end : datetime
        the last time to be forecast

    deltat : timedelta
        the time interval for the forecast

    history : pandas Series
        historical values of AC power from which the forecast is made.

    order : tuple of three integers
        autoregressive, difference, and moving average orders for an ARMA
        forecast model

    dataWindowLenth : timedelta, default one hour

    """

    # TODO: input validation

    fitdata, f_intervals = _get_data_for_ARMA_forecast(pvobj, start, end,
                                                       deltat, history,
                                                       dataWindowLength)

    # TODO: model identification logic

    # fit model of order (p, d, q)
    # defaults:
    if not order:
        if deltat.total_seconds()>=15*60:
            # use MA process for time intervals 15 min or longer
            p = 0
            d = 1
            q = 1
        else:
            # use AR process
            p = 1
            d = 1
            q = 0
        order = (p, d, q)

    model = sm.tsa.statespace.SARIMAX(fitdata,
                                      trend='n',
                                      order=order)
    results = model.fit()

    f = results.forecast(f_intervals)

    # create datetime index for forecast
    fdr = pd.DatetimeIndex(start=start, end=end, freq=pd.to_timedelta(deltat))

    return f[fdr]

#    def ARMA_one_step_forecast(self, y, column='Actual', p=1, q=0):
#        """
#        Make a one-step forecast from data in dataframe y. The data in y is differenced once. The default
#        ARMA parameters are p=1, q=0.
#        :param y: pandas dataframe
#        :param column: A valid column name in the dataframe y to forecast
#        :return: f a one-step ahead forecast for y
#        """
#        ARMA = sm.tsa.ARMA(y[column], (p, 1, q)).fit()
#        f = ARMA.forecast(1)
#
#        return f
#
#    def ARMA_seasonal_forecast(self, et, y, windowLength=timedelta(days=7), seasonalPeriod=96,
#                               tf='%Y_%m_%d_%H%M', saveModel=True):
#        """
#
#        :param et: datetime end time for end of the forecast period.
#        :param y:  dataframe object containing at least the same length of data as the window length.
#        :param windowLength: The number of days to include in building the forecast
#        :param seasonalPeriod: The number of lags before the identified season begins.
#        :param tf: time format string for loading/saving model files.
#        :param saveModel: bool flag for saving model seasonal ARMA model results.
#        :return: pandas dataframe containig forecast for the next i points after et in y.
#        """
#        while True:
#            try:
#                st = et - windowLength
#                d = y.loc[st:et]
#                d.to_csv('C:\\python\\forecasting\\d_temp.csv')
#                rel = 'ARMA_model_files\\'
#                fname = rel + st.strftime(tf) + '_to_' + et.strftime(tf) + '.pickle'
#                model = sm.tsa.statespace.SARMAX(d['Actual'], trend='n', order=(0, 1, 0),
#                                                  seasonal_order=(1, 1, 1, seasonalPeriod))
#                if os.path.isfile(fname):
#                    results = sm.load(fname)
#                else:
#                    results = model.fit()
#
#                if saveModel:
#                    results.save(fname)
#
#                f = results.forecast(seasonalPeriod)
#                break  # will break while true when stable forecast has been made.
#            except Exception as e:
#                print(e)
#                print('SHORT FORECAST: Calculating new parameters...')
#                windowLength = windowLength - timedelta(days=1)
#        return f
#
#    def ARMA(self, y, st, et, uptodate):
#        """
#
#        :param y: pandas dataframe. Historical data to use for forecasting.
#        :param starttime: datetime. The time to begin forecasting at.
#        :param endtime:  datetime. Time to end forecasting and return
#        :param uptodate: bool. Indicating the historical file is up to date with current time.
#        :return: pandas dataframe. Data frame with updated forecast and actual
#        """
#
#        # constants
#        utc = pytz.utc
#        mountain = pytz.timezone('US/Mountain')
#        # tag = 'Meter_PV REC KW'  # tag for the pi data
#        # col = ['datetime_utc', 'Actual']  # column names for dataframe
#        forecast = 'ACPowerForecast'
#
#        # update forecast
#        ptr = st
#        while ptr != et + timedelta(minutes=15):
#            w = y.loc[ptr - timedelta(hours=1, minutes=45):ptr]
#            try:
#                f = self.ARMA_one_step_forecast(w)
#                if f[0] < 0:
#                    y.loc[ptr + timedelta(minutes=15), forecast] = 0
#                else:
#                    y.loc[ptr + timedelta(minutes=15), forecast] = f[0]
#            except Exception as e:
#                y.loc[ptr + timedelta(minutes=15), forecast] = 0
##                print(ptr)
##                print(e)
#                pass
#
#            # step forward
#            ptr = ptr + timedelta(minutes=15)
#
#        # Add the latest forecast datetime column
#        y.loc[ptr, 'datetime_utc'] = ptr.strftime('%m/%d/%Y %H:%M:%S')
#
#        # fill the forecast column with the rest of the previous day-ahead seasonal arma forecast.
#        if not uptodate:
#            curTime = self.checkTime()
#            localTime = curTime.astimezone(mountain)
#
#            yesterdayMidnight = datetime(year=localTime.year, month=localTime.month, day=localTime.day,
#                                         hour=0, minute=0, second=0, microsecond=0, tzinfo=mountain)
#
#            f = self.ARMA_seasonal_forecast(yesterdayMidnight, y, seasonalPeriod=96)
#            f = f.to_frame('ACPowerForecast')
#            f.loc[f['ACPowerForecast'] < 5] = 0
#            f['datetime_utc'] = f.index.strftime('%m/%d/%Y %H:%M:%S')
#            y = y.combine_first(f.loc[max(y.index) + timedelta(minutes=15):])
#
#        return y
#
#    def ClearSkyUpperBoundForecast(self, dr, m, SiteInformation, ModuleParameters, Inverter, NumInv):
#        """
#        Creates clear sky upper bound forecast from measured and clear sky data.
#
#        :param m: dataframe of measured weather station temperature and wind speed
#        :param SiteInformation: dictionary['latitude', 'longitude', 'tz', 'altitude']
#        :param ModuleParameters: pvlib pv system
#        :param Inverter: pvlib inverter from inverter database
#        :param NumInv: number of inverters
#        :return: dataframe 'ACPowerForecast' column
#        """
#
#        # calculate solar position (sp) and clearsky irradiance (cs) for date range dr
#        cs, sp = self.ClearSkyModel(dr, SiteInformation)
#
#        # transfer values of temperature and windspeed from input m to dataframe cs
#        Ta = np.array(m['Temperature'].values)
#        WS = np.array(m['WindSpeed'].values)
#        while len(Ta)<len(dr):
#            Ta = np.concatenate((Ta, m['Temperature'].values))
#            WS = np.concatenate((WS, m['WindSpeed'].values))
#        Ta = Ta[0:len(dr)]
#        WS = WS[0:len(dr)]
#        cs['Temperature'] = Ta
#        cs['WindSpeed'] = WS
#
#        cs = cs.combine_first(sp)
#        f = IrradtoPower(cs, SiteInformation, ModuleParameters, Inverter, NumInv)
#        f['CSACPowerForecast'] = f['ACPowerForecast']
#
#        tf = [x.strftime('%m/%d/%Y %H:%M:%S') for x in f.index]
#        f['datetime_utc'] = tf
#
#        ## Adjust ac power forecast for clear index
#        # This will enforce the clear index to one when dividing measured/forecast to be 1
#        # cs.loc[sp['zenith'] > 85, 'ACPowerForecast'] = m.loc[sp['zenith'] > 85, 'Actual']
#
#        # Adjust the clear sky ac power forecast for prediction
#        # write in 0 for dark hours
#        f.loc[sp['zenith'] >= 90, 'CSACPowerForecast'] = 0
#        # replace time interval near sunrise and sunset with linear interpolation
#        f.loc[(sp['zenith'] > 85) & (sp['zenith'] < 90), 'CSACPowerForecast'] = float('nan')
#        # Ensure the beginning of the interval is not nan to allow interpolation
#        if pd.isnull(f.loc[min(dr), 'CSACPowerForecast']):
#            f.loc[min(dr), 'CSACPowerForecast'] = 0
#        f = f.interpolate(method='time')
#        # remove negative values that occur at very low irradiance levels
#        f.loc[f['CSACPowerForecast'] < 0, 'CSACPowerForecast'] = 0
#        f = f[['datetime_utc', 'CSACPowerForecast']]
#        # return f[['ACPowerForecast', 'CSACPowerForecast']]
#        return f
#
#
#    def calc_clearsky_index(self, meas, ub):
#
#        # Compute the clear index as a ratio of meas to ub
#        # returns a np.array
#
#        try:
#            clearIndex = np.true_divide(meas['Actual'].values, ub['CSACPowerForecast'].values)
#        except RuntimeWarning:
#            print('SHORT FORECAST: RuntimeWarning - likely dividing by zero.')
#            clearIndex = 0
#        clearIndex[clearIndex == np.inf] = 1.0
#        clearIndex[np.isnan(clearIndex)] = 1.0
#        clearIndex[clearIndex > 1.0] = 1.0
#
#        ci = pd.DataFrame(index=meas.index, columns=['clearIndex'])
#        ci['clearIndex'] = clearIndex
#
#        return np.array(ci['clearIndex'].values)
#
#    def compile_kt(self, dr, ci, sr_ss_window=timedelta(hours=1.5)):
#
#        # returns a dataframe containing kt values using dr as the index.
#        # dr is a pandas Datetimeindex
#        # ci is a list of values to repeat to form kt
#        # sr_ss_window is a timedelta. For this period after sunrise, or before
#        # sunset, kt values are overwritten by 1
#
#        if not dr.freq:
#            if len(dr)>3:
#                tmpfreq = pd.infer_freq(dr)
#            elif len(dr)==2:
#                tmpfreq = dr[1] - dr[0]
#            else:
#                tmpfreq = timedelta(minutes=15) # default time step
#        else:
#            tmpfreq = dr.freq
#
#        kt = np.array([])
#        # make sure kt is same length as period to be forecast
#        while len(kt) < len(dr):
#            kt = np.concatenate((kt, ci))
#        kt = kt[0:len(dr)]
#
#        df = pd.DataFrame(index=dr)
#        df['kt'] = kt
#
#        # overwrite sunrise/sunset hours with clear sky model
#        # extend dr each end in case first/last values are within a sunrise/sunset window
#        tdr = dr.union(pd.DatetimeIndex(start=min(dr - sr_ss_window), end=min(dr), freq = tmpfreq))
#        tdr = tdr.union(pd.DatetimeIndex(start=max(tdr), end=max(dr + sr_ss_window), freq = tmpfreq))
#
#        # find sunrise/sunset hours
#        sp = pvlib.solarposition.ephemeris(tdr, self.siteInfo['latitude'], self.siteInfo['longitude'])
#        dl = sp['zenith'].values<90
#        sr = dl[1:] & (dl[:-1] != dl[1:])
#        sr = np.append(False, sr)
#        ss = dl[:-1] & (dl[:-1] != dl[1:])
#        ss = np.append(ss, False)
#        sridx = sp.index[sr]
#        ssidx = sp.index[ss]
#        df = df.combine_first(pd.DataFrame(index=tdr))
#        for i in sridx:
#            u = (tdr > i) & (tdr < i + sr_ss_window)
#            df.loc[tdr[u], 'kt'] = 1
#        for i in ssidx:
#            u = (tdr < i) & (tdr > i - sr_ss_window)
#            df.loc[tdr[u], 'kt'] = 1
#
#        return df
#
#    def PersistenceForecast(self, y, kt, dr, f_upperbound):
#        # generates a forecast for times in dr using clearsky index in kt and f_upperbound.
#        # Data in kt are tiled to fill the length
#        # of the requested forecast dr.  Clearness index is multiplied by f_upperbound
#        # to produce the forecast, so it is implicit that f_upperbound covers the period
#        # specified by dr.
#        # returns dataframe 'y' which accumulates the history of forecast and actual values
#
#
#        f_upper = f_upperbound.loc[dr, ['datetime_utc', 'CSACPowerForecast']]
#
#        # add kt column to f_upper
#        tmp = self.compile_kt(dr, kt)
#
#        # Now multiply
#        f_upper['ACPowerForecast'] = f_upper['CSACPowerForecast'] * tmp['kt']
#        # extend y with new index values
#        y = y.combine_first(pd.DataFrame(index=dr))
#        # overwrite forecast values for date range dr
#        y.loc[dr, ['ACPowerForecast', 'datetime_utc']] = f_upper.loc[dr, ['ACPowerForecast', 'datetime_utc']]
#
#        return y

if __name__ == "__main__":

    USMtn = pytz.timezone('US/Mountain')
    if pvlib.__version__ < '0.4.1':
        print('pvlib out-of-date, found version ' + pvlib.__version__ +
              ', please upgrade to 0.4.1 or later')
    else:
        # make a dict of PV system objects
        pvdict = {};
        pvdict['Prosperity'] = PVobj('Prosperity',
                                     dc_capacity=500,
                                     ac_capacity=480,
                                     lat=35.04,
                                     lon=-106.62,
                                     alt=1619,
                                     tz=USMtn,
                                     tilt=25,
                                     azimuth=180,
                                     forecast_method='ARMA')
        pvdict['Prosperityx2'] = PVobj('Prosperity',
                                     dc_capacity=1000,
                                     ac_capacity=960,
                                     lat=35.04,
                                     lon=-106.62,
                                     alt=1619,
                                     tz=USMtn,
                                     tilt=35,
                                     azimuth=240,
                                     forecast_method='persistence')

        plt.plot(pvdict['Prosperity'].clearsky['ac_power'][:1440])
        plt.show()

        plt.plot(pvdict['Prosperityx2'].clearsky['ac_power'][:1440])
        plt.show()

        pvobj = pvdict['Prosperityx2']
        
        # use clearsky model as a surrogate for system data
        hstart = datetime(2016, 2, 3, 0, 0, 30, tzinfo=USMtn)
        hend = datetime(2016, 3, 6, 11, 50, 30, tzinfo=USMtn)
        dat = pvobj.clearsky['ac_power']
        history = dat.loc[(dat.index>=hstart) & (dat.index<hend)]

        # forecast period
        fstart = datetime(2016, 2, 16, 17, 3, 0, tzinfo=USMtn)
        fend = fstart + timedelta(minutes=60)
        fcstP = pvobj.forecast(start=fstart,
                             end=fend,
                             history=history,
                             deltat=timedelta(minutes=15),
                             dataWindowLength=timedelta(hours=2))

        # for plotting
        hst = history[(history.index>=(fstart - timedelta(hours=3))) & (history.index<fstart)]
        dateFormatter = mdates.DateFormatter('%H:%M')
        plt.gca().xaxis.set(major_formatter=dateFormatter)
        plt.xticks(rotation=70)
        plt.plot(hst, 'b-')
        plt.plot(fcstP, 'rx')
        plt.show()
