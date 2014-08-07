#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# Filename: array_analysis.py
#  Purpose: Functions for Array Analysis
#   Author: Martin van Driel, Moritz Beyreuther, Joachim Wassermann
#    Email: j.wassermann@lmu.de
#
# Copyright (C) 2010-2014 Martin van Driel, Moritz Beyreuther,
#                         Joachim Wassermann
# --------------------------------------------------------------------
"""
Functions for Array Analysis


Coordinate conventions:

* Right handed
* X positive to east
* Y positive to north
* Z positive up

:copyright:
    The ObsPy Development Team (devs@obspy.org)
:license:
    GNU Lesser General Public License, Version 3
    (http://www.gnu.org/copyleft/lesser.html)
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
from future.builtins import *  # NOQA

import collections
import copy
import math
import tempfile
import os
import shutil
import numpy as np
import scipy as sp
from scipy import interpolate
from matplotlib import cm
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from obspy.core import UTCDateTime
from obspy.core.util.geodetics import gps2DistAzimuth, KM_PER_DEG
from obspy.signal.util import utlGeoKm, nextpow2
from obspy.signal.headers import clibsignal
from obspy.core import Stream, Trace
from scipy.integrate import cumtrapz
from obspy.signal.invsim import cosTaper
from obspy.core.util import AttribDict
import warnings


class SeismicArray(object):
    """
    Class representing a seismic array.up
    """
    def __init__(self, name=u"",):
        self.name = name
        self.inventory = None

    def add_inventory(self, inv):
        if self.inventory is not None:
            raise NotImplementedError
        self.inventory = inv

    def plot(self):
        import matplotlib.pylab as plt
        if self.inventory:
            self.inventory.plot(projection="local", show=False)
            bmap = plt.gca().basemap

            grav = self.center_of_gravity
            x, y = bmap(grav["longitude"], grav["latitude"])
            bmap.scatter(x, y, marker="x", c="red", s=40, zorder=20)
            plt.text(x, y, "Center of Gravity", color="red")

            geo = self.geometrical_center
            x, y = bmap(geo["longitude"], geo["latitude"])
            bmap.scatter(x, y, marker="x", c="green", s=40, zorder=20)
            plt.text(x, y, "Geometrical Center", color="green")

            plt.show()

    def _get_geometry(self):
        if not self.inventory:
            return {}

        geo = {}
        for network in self.inventory:
            for station in network:
                station_code = "{n}.{s}".format(n=network.code,
                                                s=station.code)
                for channel in station:
                    this_coordinates = \
                        {"latitude": float(channel.latitude),
                         "longitude": float(channel.longitude),
                         "absolute_height_in_km":
                         float(channel.elevation - channel.depth) / 1000.0}
                    if station_code in geo and \
                            this_coordinates not in geo.values():
                        msg = ("Different coordinates for station '{n}.{s}' "
                               "in the inventory. The first ones encountered "
                               "will be chosen.".format(n=network.code,
                                                        s=station.code))
                        warnings.warn(msg)
                        continue
                    geo[station_code] = this_coordinates
        return geo

    @property
    def geometrical_center(self):
        extend = self.extend
        return {
            "latitude": (extend["max_latitude"] +
                         extend["min_latitude"]) / 2.0,
            "longitude": (extend["max_longitude"] +
                          extend["min_longitude"]) / 2.0,
            "absolute_height_in_km":
            (extend["min_absolute_height_in_km"] +
             extend["max_absolute_height_in_km"]) / 2.0
        }

    @property
    def center_of_gravity(self):
        lats, lngs, hgts = self.__coordinate_values()
        return {
            "latitude": np.mean(lats),
            "longitude": np.mean(lngs),
            "absolute_height_in_km": np.mean(hgts)}

    @property
    def geometry(self):
        return self._get_geometry()

    @property
    def aperture(self):
        """
        The aperture of the array in kilometers.
        """
        distances = []
        geo = self.geometry
        for station, coordinates in geo.items():
            for other_station, other_coordinates in geo.items():
                if station == other_station:
                    continue
                distances.append(gps2DistAzimuth(
                    coordinates["latitude"], coordinates["longitude"],
                    other_coordinates["latitude"],
                    other_coordinates["longitude"])[0] / 1000.0)

        return max(distances)

    @property
    def extend(self):
        lats, lngs, hgt = self.__coordinate_values()

        return {
            "min_latitude": min(lats),
            "max_latitude": max(lats),
            "min_longitude": min(lngs),
            "max_longitude": max(lngs),
            "min_absolute_height_in_km": min(hgt),
            "max_absolute_height_in_km": max(hgt)}

    def __coordinate_values(self):
        geo = self.geometry
        lats, lngs, hgt = [], [], []
        for coordinates in geo.values():
            lats.append(coordinates["latitude"]),
            lngs.append(coordinates["longitude"]),
            hgt.append(coordinates["absolute_height_in_km"])
        return lats, lngs, hgt

    def __unicode__(self):
        """
        Pretty representation of the array.
        """
        ret_str = u"Seismic Array '{name}'\n".format(name=self.name)
        ret_str += u"\t{count} Stations\n".format(count=len(self.geometry))
        ret_str += u"\tAperture: {aperture:.2f} km".format(
            aperture=self.aperture)
        return ret_str

    def __str__(self):
        """
        Stub calling the unicode method.

        See here:
        http://stackoverflow.com/questions/1307014/python-str-versus-unicode
        """
        return unicode(self).encode("utf-8")

    def get_geometry_xyz(self, latitude, longitude, absolute_height_in_km,
                         correct_3dplane=False):
        """
        Method to calculate the array geometry and the center coordinates in km

        :param correct_3dplane: applies a 3D best fitting plane to the array.
               This might be important if the array is located on a inclinde
               slope (e.g., at a volcano)
        :return: Returns the geometry of the stations as 2d numpy.ndarray
                The first dimension are the station indexes with the same order
                as the traces in the stream object. The second index are the
                values of [lat, lon, elev] in km
                last index contains center [lat, lon, elev] in degrees and
                km if return_center is true
        """
        geometry = {}

        for key, value in self.geometry.items():
            x, y = utlGeoKm(longitude, latitude, value["longitude"],
                            value["latitude"])
            geometry[key] = {
                "x": x,
                "y": y,
                "z": absolute_height_in_km - value["absolute_height_in_km"]
            }

        # XXX: Adjust to dictionary based distances!!
        if correct_3dplane:
            A = geometry
            u, s, vh = np.linalg.linalg.svd(A)
            v = vh.conj().transpose()
            # satisfies the plane equation a*x + b*y + c*z = 0
            result = np.zeros((nstat, 3))
            # now we are seeking the station positions on that plane
            # geometry[:,2] += v[2,-1]
            n = v[:, -1]
            result[:, 0] = (geometry[:, 0] - n[0] * (
                n[0] * geometry[:, 0] + geometry[:, 1] * n[1] + n[2] *
                geometry[:, 2]) / (
                                n[0] * n[0] + n[1] * n[1] + n[2] * n[2]))
            result[:, 1] = (geometry[:, 1] - n[1] * (
                n[0] * geometry[:, 0] + geometry[:, 1] * n[1] + n[2] *
                geometry[:, 2]) / (
                                n[0] * n[0] + n[1] * n[1] + n[2] * n[2]))
            result[:, 2] = (geometry[:, 2] - n[2] * (
                n[0] * geometry[:, 0] + geometry[:, 1] * n[1] + n[2] *
                geometry[:, 2]) / (
                                n[0] * n[0] + n[1] * n[1] + n[2] * n[2]))
            geometry = result[:]
            print("Best fitting plane-coordinates :", geometry)

        return geometry

    def find_closest_station(self, latitude, longitude, elevation_in_m=0.0,
                             local_depth_in_m=0.0):
        min_distance = None
        min_distance_station = None

        true_elevation = elevation_in_m - local_depth_in_m

        for key, value in self.geometry.items():
            # output in [km]
            x, y = utlGeoKm(longitude, latitude, value["longitude"],
                            value["latitude"])
            x *= 1000.0
            y *= 1000.0

            true_sta_elevation = value["elevation_in_m"] - \
                value["local_depth_in_m"]
            z = true_elevation - true_sta_elevation

            distance = np.sqrt(x ** 2 + y ** 2 + z ** 2)
            if min_distance is None or distance < min_distance:
                min_distance = distance
                min_distance_station = key

        return min_distance_station

    def get_timeshift_baz(self, sll, slm, sls, baz, latitude, longitude,
                          absolute_height_in_km, static_3D=False,
                          vel_cor=4.0):
        """
        Returns timeshift table for given array geometry and a pre-defined
        backazimuth.

        :param sll_x: slowness x min (lower)
        :param slm_y: slowness x max (lower)
        :param sl_s: slowness step
        :param baz:  backazimuth applied
        :param vel_cor: correction velocity (upper layer) in km/s
        :param static_3D: a correction of the station height is applied using
            vel_cor the correction is done according to the formula:
            t = rxy*s - rz*cos(inc)/vel_cor
            where inc is defined by inv = asin(vel_cor*slow)
        """
        geom = self.get_geometry_xyz(latitude, longitude,
                                     absolute_height_in_km)

        baz = math.pi * baz / 180.0

        time_shift_tbl = {}
        sx = sll
        while sx < slm:
            try:
                inc = math.asin(vel_cor * sx)
            except ValueError:
                 inc = np.pi / 2.0

            time_shifts = {}
            for key, value in geom.items():
                time_shifts[key] = sx * (value["x"] * math.sin(baz) +
                                         value["y"] * math.cos(baz))

                if static_3D:
                    try:
                        v = vel_cor[key]
                    except TypeError:
                        v = vel_cor
                    time_shifts[key] += value["z"] * math.cos(inc) / v

                time_shift_tbl[sx] = time_shifts
            sx += sls

        return time_shift_tbl

    def get_timeshift(geometry, sll_x, sll_y, sl_s, grdpts_x,
                      grdpts_y, vel_cor=4., static_3D=False):
        """
        Returns timeshift table for given array geometry

        :param geometry: Nested list containing the arrays geometry,
            as returned by get_group_geometry
        :param sll_x: slowness x min (lower)
        :param sll_y: slowness y min (lower)
        :param sl_s: slowness step
        :param grdpts_x: number of grid points in x direction
        :param grdpts_x: number of grid points in y direction
        :param vel_cor: correction velocity (upper layer) in km/s
        :param static_3D: a correction of the station height is applied using
            vel_cor the correction is done according to the formula:
            t = rxy*s - rz*cos(inc)/vel_cor
            where inc is defined by inv = asin(vel_cor*slow)
        """
        if static_3D:
            nstat = len(geometry)  # last index are center coordinates
            time_shift_tbl = np.empty((nstat, grdpts_x, grdpts_y), dtype="float32")
            for k in xrange(grdpts_x):
                sx = sll_x + k * sl_s
                for l in xrange(grdpts_y):
                    sy = sll_y + l * sl_s
                    slow = np.sqrt(sx*sx + sy*sy)
                    if vel_cor*slow <= 1.:
                        inc = np.arcsin(vel_cor*slow)
                    else:
                        print ("Warning correction velocity smaller than apparent "
                               "velocity")
                        inc = np.pi/2.
                    time_shift_tbl[:, k, l] = sx * geometry[:, 0] + sy * \
                                                                    geometry[:, 1] + geometry[:, 2] * np.cos(inc) / vel_cor
            return time_shift_tbl
        # optimized version
        else:
            mx = np.outer(geometry[:, 0], sll_x + np.arange(grdpts_x) * sl_s)
            my = np.outer(geometry[:, 1], sll_y + np.arange(grdpts_y) * sl_s)
            return np.require(
                mx[:, :, np.newaxis].repeat(grdpts_y, axis=2) +
                my[:, np.newaxis, :].repeat(grdpts_x, axis=1),
                dtype='float32')

    def vespagram(self, stream, event_or_baz, sll, slm, sls, starttime,
                  endtime, latitude, longitude, absolute_height_in_km,
                  method="DLS", nthroot=1, static_3D=False, vel_cor=4.0):
        baz = float(event_or_baz)
        time_shift_table = self.get_timeshift_baz(
            sll, slm, sls, baz, latitude, longitude, absolute_height_in_km,
            static_3D=static_3D, vel_cor=vel_cor)

        vg = vespagram_baz(stream, time_shift_table, starttime=starttime,
                           endtime=endtime, method=method, nthroot=nthroot)

    def derive_rotation_from_array(self, stream, vp, vs, sigmau, latitude,
                                   longitude, elevation_in_m=0.0,
                                   local_depth_in_m=0.0):
        geo = self.geometry

        components = collections.defaultdict(list)
        for tr in stream:
            components[tr.stats.channel[-1].upper()].append(tr)

        # Sanity checks.
        if sorted(components.keys()) != ["E", "N", "Z"]:
            raise ValueError("Three components necessary.")

        for value in components.values():
            value.sort(key=lambda x: "%s.%s" % (x.stats.network,
                                                x.stats.station))

        ids = [tuple([_i.id[:-1] for _i in traces]) for traces in
               components.values()]
        if len(set(ids)) != 1:
            raise ValueError("All stations need to have three components.")

        stats = [[(_i.stats.starttime.timestamp, _i.stats.npts,
                   _i.stats.sampling_rate)
                  for _i in traces] for traces in components.values()]
        s = []
        for st in stats:
            s.extend(st)

        if len(set(s)) != 1:
            raise ValueError("starttime, npts, and sampling rate must be "
                             "identical for all traces.")

        stations = ["%s.%s" % (_i.stats.network, _i.stats.station)
                    for _i in components.values()[0]]
        for station in stations:
            if station not in geo:
                raise ValueError("No coordinates known for station '%s'" %
                                 station)

        array_coords = np.ndarray(shape=(len(geo), 3))
        for _i, tr in enumerate(components.values()[0]):
            station = "%s.%s" % (tr.stats.network, tr.stats.station)

            x, y = utlGeoKm(longitude, latitude,
                            geo[station]["longitude"],
                            geo[station]["latitude"])
            z = (elevation_in_m - local_depth_in_m) - \
                (geo[station]["elevation_in_m"] -
                 geo[station]["local_depth_in_m"])
            array_coords[_i][0] = x * 1000.0
            array_coords[_i][1] = y * 1000.0
            array_coords[_i][2] = z

        subarray = np.arange(len(geo))

        tr = []
        for _i, component in enumerate(["Z", "N", "E"]):
            comp = components[component]
            tr.append(np.empty((len(comp[0]), len(comp))))
            for _j, trace in enumerate(comp):
                tr[_i][:, _j][:] = np.require(trace.data, np.float64)

        sp = array_rotation_strain(subarray, tr[0], tr[1], tr[2], vp=vp,
                                   vs=vs,  array_coords=array_coords,
                                   sigmau=sigmau)

        d1 = sp.pop("ts_w1")
        d2 = sp.pop("ts_w2")
        d3 = sp.pop("ts_w3")

        header = {"network": "XX",
                  "station": "YY",
                  "location": "99",
                  "starttime": components.values()[0][0].stats.starttime,
                  "sampling_rate":
                  components.values()[0][0].stats.sampling_rate}

        header["channel"] = "ROZ"
        header["npts"] = len(d1)
        tr1 = Trace(data=d1, header=copy.copy(header))
        header["channel"] = "RON"
        header["npts"] = len(d2)
        tr2 = Trace(data=d2, header=copy.copy(header))
        header["channel"] = "ROE"
        header["npts"] = len(d3)
        tr3 = Trace(data=d3, header=copy.copy(header))

        return Stream(traces=[tr1, tr2, tr3]), sp

    def slowness_whitened_power(self, stream, frqlow, frqhigh,
                                filter=True, baz_plot=True, static3D=False,
                                vel_corr=4.8, wlen=-1, slx=(-10, 10),
                                sly=(-10, 10), sls=0.5, array_response=True):
        """
        Slowness whitened power analysis.

        :param stream: Waveforms for the array processing.
        :type stream: :class:`obspy.core.stream.Stream`
        :param filter: Whether to bandpass data to selected frequency range
        :type filter: bool
        :param frqlow: Low corner of frequency range for array analysis
        :type frqlow: float
        :param frqhigh: High corner of frequency range for array analysis
        :type frqhigh: float
        :param baz_plot: Whether to show backazimuth-slowness map (True) or
         slowness x-y map (False).
        :type baz_plot: str
        :param static3D: static correction of topography using `vel_corr` as
         velocity (slow!)
        :type static3D: bool
        :param vel_corr: Correction velocity for static topography correction in
         km/s.
        :type vel_corr: float
        :param wlen: sliding window for analysis in seconds, use -1 to use the
         whole trace without windowing.
        :type wlen: float
        :param slx: Min/Max slowness for analysis in x direction.
        :type slx: (float, float)
        :param sly: Min/Max slowness for analysis in y direction.
        :type sly: (float, float)
        :param sls: step width of slowness grid
        :type sls: float
        :param array_response: superimpose array reponse function in plot (slow!)
        :type array_response: bool
        """
        return self._array_analysis_helper(stream=stream, method="SWP",
                                           frqlow=frqlow, frqhigh=frqhigh,
                                           filter=filter, baz_plot=baz_plot,
                                           static3D=static3D,
                                           vel_corr=vel_corr, wlen=wlen,
                                           slx=slx, sly=sly, sls=sls,
                                           array_response=array_response)

    def phase_weighted_stack(self, stream, frqlow, frqhigh,
                             filter=True, baz_plot=True, static3D=False,
                             vel_corr=4.8, wlen=-1, slx=(-10, 10),
                             sly=(-10, 10), sls=0.5, array_response=True):
        """
        Phase weighted stack analysis.

        :param stream: Waveforms for the array processing.
        :type stream: :class:`obspy.core.stream.Stream`
        :param filter: Whether to bandpass data to selected frequency range
        :type filter: bool
        :param frqlow: Low corner of frequency range for array analysis
        :type frqlow: float
        :param frqhigh: High corner of frequency range for array analysis
        :type frqhigh: float
        :param baz_plot: Whether to show backazimuth-slowness map (True) or
         slowness x-y map (False).
        :type baz_plot: str
        :param static3D: static correction of topography using `vel_corr` as
         velocity (slow!)
        :type static3D: bool
        :param vel_corr: Correction velocity for static topography correction in
         km/s.
        :type vel_corr: float
        :param wlen: sliding window for analysis in seconds, use -1 to use the
         whole trace without windowing.
        :type wlen: float
        :param slx: Min/Max slowness for analysis in x direction.
        :type slx: (float, float)
        :param sly: Min/Max slowness for analysis in y direction.
        :type sly: (float, float)
        :param sls: step width of slowness grid
        :type sls: float
        :param array_response: superimpose array reponse function in plot (slow!)
        :type array_response: bool
        """
        return self._array_analysis_helper(stream=stream, method="PWS",
                                           frqlow=frqlow, frqhigh=frqhigh,
                                           filter=filter, baz_plot=baz_plot,
                                           static3D=static3D,
                                           vel_corr=vel_corr, wlen=wlen,
                                           slx=slx, sly=sly, sls=sls,
                                           array_response=array_response)

    def delay_and_sum(self, stream, frqlow, frqhigh,
                     filter=True, baz_plot=True, static3D=False,
                     vel_corr=4.8, wlen=-1, slx=(-10, 10),
                     sly=(-10, 10), sls=0.5, array_response=True):
        """
        Delay and sum analysis.

        :param stream: Waveforms for the array processing.
        :type stream: :class:`obspy.core.stream.Stream`
        :param filter: Whether to bandpass data to selected frequency range
        :type filter: bool
        :param frqlow: Low corner of frequency range for array analysis
        :type frqlow: float
        :param frqhigh: High corner of frequency range for array analysis
        :type frqhigh: float
        :param baz_plot: Whether to show backazimuth-slowness map (True) or
         slowness x-y map (False).
        :type baz_plot: str
        :param static3D: static correction of topography using `vel_corr` as
         velocity (slow!)
        :type static3D: bool
        :param vel_corr: Correction velocity for static topography correction in
         km/s.
        :type vel_corr: float
        :param wlen: sliding window for analysis in seconds, use -1 to use the
         whole trace without windowing.
        :type wlen: float
        :param slx: Min/Max slowness for analysis in x direction.
        :type slx: (float, float)
        :param sly: Min/Max slowness for analysis in y direction.
        :type sly: (float, float)
        :param sls: step width of slowness grid
        :type sls: float
        :param array_response: superimpose array reponse function in plot (slow!)
        :type array_response: bool
        """
        return self._array_analysis_helper(stream=stream, method="DLS",
                                           frqlow=frqlow, frqhigh=frqhigh,
                                           filter=filter, baz_plot=baz_plot,
                                           static3D=static3D,
                                           vel_corr=vel_corr, wlen=wlen,
                                           slx=slx, sly=sly, sls=sls,
                                           array_response=array_response)

    def fk_analysis(self, stream, frqlow, frqhigh,
                    filter=True, baz_plot=True, static3D=False,
                    vel_corr=4.8, wlen=-1, slx=(-10, 10),
                    sly=(-10, 10), sls=0.5, array_response=True):
        """
        FK analysis.

        :param stream: Waveforms for the array processing.
        :type stream: :class:`obspy.core.stream.Stream`
        :param filter: Whether to bandpass data to selected frequency range
        :type filter: bool
        :param frqlow: Low corner of frequency range for array analysis
        :type frqlow: float
        :param frqhigh: High corner of frequency range for array analysis
        :type frqhigh: float
        :param baz_plot: Whether to show backazimuth-slowness map (True) or
         slowness x-y map (False).
        :type baz_plot: str
        :param static3D: static correction of topography using `vel_corr` as
         velocity (slow!)
        :type static3D: bool
        :param vel_corr: Correction velocity for static topography correction in
         km/s.
        :type vel_corr: float
        :param wlen: sliding window for analysis in seconds, use -1 to use the
         whole trace without windowing.
        :type wlen: float
        :param slx: Min/Max slowness for analysis in x direction.
        :type slx: (float, float)
        :param sly: Min/Max slowness for analysis in y direction.
        :type sly: (float, float)
        :param sls: step width of slowness grid
        :type sls: float
        :param array_response: superimpose array reponse function in plot (slow!)
        :type array_response: bool
        """
        return self._array_analysis_helper(stream=stream, method="FK",
                                           frqlow=frqlow, frqhigh=frqhigh,
                                           filter=filter, baz_plot=baz_plot,
                                           static3D=static3D,
                                           vel_corr=vel_corr, wlen=wlen,
                                           slx=slx, sly=sly, sls=sls,
                                           array_response=array_response)

    def _array_analysis_helper(self, stream, method, frqlow, frqhigh,
                               filter=True, baz_plot=True, static3D=False,
                               vel_corr=4.8, wlen=-1, slx=(-10, 10),
                               sly=(-10, 10), sls=0.5, array_response=True):
        """
        Array analysis wrapper routine.

        :param stream: Waveforms for the array processing.
        :type stream: :class:`obspy.core.stream.Stream`
        :param method: Method used for the array analysis
            (one of "FK": Frequency Wavenumber, "DLS": Delay and Sum,
            "PWS": Phase Weighted Stack, "SWP": Slowness Whitened Power).
        :type method: str
        :param filter: Whether to bandpass data to selected frequency range
        :type filter: bool
        :param frqlow: Low corner of frequency range for array analysis
        :type frqlow: float
        :param frqhigh: High corner of frequency range for array analysis
        :type frqhigh: float
        :param baz_plot: Whether to show backazimuth-slowness map (True) or
         slowness x-y map (False).
        :type baz_plot: str
        :param static3D: static correction of topography using `vel_corr` as
         velocity (slow!)
        :type static3D: bool
        :param vel_corr: Correction velocity for static topography correction in
         km/s.
        :type vel_corr: float
        :param wlen: sliding window for analysis in seconds, use -1 to use the
         whole trace without windowing.
        :type wlen: float
        :param slx: Min/Max slowness for analysis in x direction.
        :type slx: (float, float)
        :param sly: Min/Max slowness for analysis in y direction.
        :type sly: (float, float)
        :param sls: step width of slowness grid
        :type sls: float
        :param array_response: superimpose array reponse function in plot (slow!)
        :type array_response: bool
        """

        if method not in ("FK", "DLS", "PWS", "SWP"):
            raise ValueError("Invalid method: ''" % method)

        sllx, slmx = slx
        slly, slmy = sly

        starttime = max([tr.stats.starttime for tr in stream])
        endtime = min([tr.stats.endtime for tr in stream])
        stream.trim(starttime, endtime)

        #stream.attach_response(inventory)
        stream.merge()
        self._attach_coords_to_stream(stream)

        if filter:
            stream.filter('bandpass', freqmin=frqlow, freqmax=frqhigh,
                          zerophase=True)

        spl = stream.copy()

        tmpdir = tempfile.mkdtemp(prefix="obspy-")
        filename_patterns = (os.path.join(tmpdir, 'pow_map_%03d.npy'),
                             os.path.join(tmpdir, 'apow_map_%03d.npy'))

        def dump(pow_map, apow_map, i):
            np.save(filename_patterns[0] % i, pow_map)
            np.save(filename_patterns[1] % i, apow_map)

        try:
            # next step would be needed if the correction velocity needs to be
            # estimated
            #
            sllx /= KM_PER_DEG
            slmx /= KM_PER_DEG
            slly /= KM_PER_DEG
            slmy /= KM_PER_DEG
            sls /= KM_PER_DEG
            vc = vel_corr
            if method == 'FK':
                kwargs = dict(
                    #slowness grid: X min, X max, Y min, Y max, Slow Step
                    sll_x=sllx, slm_x=slmx, sll_y=slly, slm_y=slmy, sl_s=sls,
                    # sliding window properties
                    win_len=wlen, win_frac=0.8,
                    # frequency properties
                    frqlow=frqlow, frqhigh=frqhigh, prewhiten=0,
                    # restrict output
                    store=dump,
                    semb_thres=-1e9, vel_thres=-1e9, verbose=False,
                    timestamp='julsec', stime=starttime, etime=endtime,
                    method=0, correct_3dplane=False, vel_cor=vc,
                    static_3D=static3D)

                # here we do the array processing
                start = UTCDateTime()
                out = array_processing(stream, **kwargs)
                print("Total time in routine: %f\n" % (UTCDateTime() - start))

                # make output human readable, adjust backazimuth to values
                # between 0 and 360
                t, rel_power, abs_power, baz, slow = out.T

            else:
                kwargs = dict(
                    # slowness grid: X min, X max, Y min, Y max, Slow Step
                    sll_x=sllx, slm_x=slmx, sll_y=slly, slm_y=slmy, sl_s=sls,
                    # sliding window properties
                    # frequency properties
                    frqlow=frqlow, frqhigh=frqhigh,
                    # restrict output
                    store=dump,
                    win_len=wlen, win_frac=0.5,
                    nthroot=4, method=method,
                    verbose=False, timestamp='julsec',
                    stime=starttime, etime=endtime, vel_cor=vc,
                    static_3D=False)

                # here we do the array processing
                start = UTCDateTime()
                out = beamforming(stream, **kwargs)
                print("Total time in routine: %f\n" % (UTCDateTime() - start))

                # make output human readable, adjust backazimuth to values
                # between 0 and 360
                trace = []
                t, rel_power, baz, slow_x, slow_y, slow = out.T

                # calculating array response
            if array_response:
                stepsfreq = (frqhigh - frqlow) / 10.
                tf_slx = sllx
                tf_smx = slmx
                tf_sly = slly
                tf_smy = slmy
                transff = array_transff_freqslowness(
                    stream, (tf_slx, tf_smx, tf_sly, tf_smy), sls, frqlow,
                    frqhigh, stepsfreq, coordsys='lonlat',
                    correct_3dplane=False, static_3D=False, vel_cor=vc)

            # now let's do the plotting
            cmap = cm.rainbow

            #
            # we will plot everything in s/deg
            slow *= KM_PER_DEG
            sllx *= KM_PER_DEG
            slmx *= KM_PER_DEG
            slly *= KM_PER_DEG
            slmy *= KM_PER_DEG
            sls *= KM_PER_DEG

            numslice = len(t)
            powmap = []
            slx = np.arange(sllx-sls, slmx, sls)
            sly = np.arange(slly-sls, slmy, sls)
            if baz_plot:
                maxslowg = np.sqrt(slmx*slmx + slmy*slmy)
                bzs = np.arctan2(sls, np.sqrt(slmx*slmx + slmy*slmy))*180/np.pi
                xi = np.arange(0., maxslowg, sls)
                yi = np.arange(-180., 180., bzs)
                grid_x, grid_y = np.meshgrid(xi, yi)
            # reading in the rel-power maps
            for i in xrange(numslice):
                powmap.append(np.load(filename_patterns[0] % i))
                if method != 'FK':
                    trace.append(np.load(filename_patterns[1] % i))

            npts = stream[0].stats.npts
            df = stream[0].stats.sampling_rate
            T = np.arange(0, npts / df, 1 / df)

            # if we choose windowlen > 0. we now move through our slices
            for i in xrange(numslice):
                slow_x = np.sin((baz[i]+180.)*np.pi/180.)*slow[i]
                slow_y = np.cos((baz[i]+180.)*np.pi/180.)*slow[i]
                st = UTCDateTime(t[i]) - starttime
                if wlen <= 0:
                    en = endtime
                else:
                    en = st + wlen
                print(UTCDateTime(t[i]))
                # add polar and colorbar axes
                fig = plt.figure(figsize=(12, 12))
                ax1 = fig.add_axes([0.1, 0.87, 0.7, 0.10])
                # here we plot the first trace on top of the slowness map
                # and indicate the possibiton of the lsiding window as green box
                if method == 'FK':
                    ax1.plot(T, spl[0].data, 'k')
                    if wlen > 0.:
                        try:
                            ax1.axvspan(st, en, facecolor='g', alpha=0.3)
                        except IndexError:
                            pass
                else:
                    T = np.arange(0, len(trace[i])/df, 1 / df)
                    ax1.plot(T, trace[i], 'k')

                ax1.yaxis.set_major_locator(MaxNLocator(3))

                ax = fig.add_axes([0.10, 0.1, 0.70, 0.7])

                # if we have chosen the baz_plot option a re-griding
                # of the sx,sy slowness map is needed
                if baz_plot:
                    slowgrid = []
                    transgrid = []
                    pow = np.asarray(powmap[i])
                    for ix, sx in enumerate(slx):
                        for iy, sy in enumerate(sly):
                            bbaz = np.arctan2(sx, sy)*180/np.pi+180.
                            if bbaz > 180.:
                                bbaz = -180. + (bbaz-180.)
                            slowgrid.append((np.sqrt(sx*sx+sy*sy), bbaz,
                                             pow[ix, iy]))
                            if array_response:
                                tslow = (np.sqrt((sx+slow_x) *
                                                 (sx+slow_x)+(sy+slow_y) *
                                                 (sy+slow_y)))
                                tbaz = (np.arctan2(sx+slow_x, sy+slow_y) *
                                        180 / np.pi + 180.)
                                if tbaz > 180.:
                                    tbaz = -180. + (tbaz-180.)
                                transgrid.append((tslow, tbaz,
                                                  transff[ix, iy]))

                    slowgrid = np.asarray(slowgrid)
                    sl = slowgrid[:, 0]
                    bz = slowgrid[:, 1]
                    slowg = slowgrid[:, 2]
                    grid = interpolate.griddata((sl, bz), slowg,
                                                   (grid_x, grid_y),
                                                   method='nearest')
                    ax.pcolormesh(xi, yi, grid, cmap=cmap)

                    if array_response:
                        level = np.arange(0.1, 0.5, 0.1)
                        transgrid = np.asarray(transgrid)
                        tsl = transgrid[:, 0]
                        tbz = transgrid[:, 1]
                        transg = transgrid[:, 2]
                        trans = interpolate.griddata((tsl, tbz), transg,
                                                        (grid_x, grid_y),
                                                        method='nearest')
                        ax.contour(xi, yi, trans, level, colors='k',
                                   linewidth=0.2)

                    ax.set_xlabel('slowness [s/deg]')
                    ax.set_ylabel('backazimuth [deg]')
                    ax.set_xlim(xi[0], xi[-1])
                    ax.set_ylim(yi[0], yi[-1])
                else:
                    ax.set_xlabel('slowness [s/deg]')
                    ax.set_ylabel('slowness [s/deg]')
                    slow_x = np.cos((baz[i]+180.)*np.pi/180.)*slow[i]
                    slow_y = np.sin((baz[i]+180.)*np.pi/180.)*slow[i]
                    ax.pcolormesh(slx, sly, powmap[i].T)
                    ax.arrow(0, 0, slow_y, slow_x, head_width=0.005,
                             head_length=0.01, fc='k', ec='k')
                    if array_response:
                        tslx = np.arange(sllx+slow_x, slmx+slow_x+sls, sls)
                        tsly = np.arange(slly+slow_y, slmy+slow_y+sls, sls)
                        try:
                            ax.contour(tsly, tslx, transff.T, 5, colors='k',
                                       linewidth=0.5)
                        except:
                            pass
                    ax.set_ylim(slx[0], slx[-1])
                    ax.set_xlim(sly[0], sly[-1])
                new_time = t[i]

                result = "BAZ: %.2f, Slow: %.2f s/deg, Time %s" % (
                    baz[i], slow[i], UTCDateTime(new_time))
                ax.set_title(result)

                plt.show()
        finally:
            shutil.rmtree(tmpdir)

    def plot_transfer_function(self, stream, sx=(-10, 10),
                               sy=(-10, 10), sls=0.5, freqmin=0.1, freqmax=4.0,
                               numfreqs=10, coordsys='lonlat',
                               correct3dplane=False, static3D=False,
                               velcor=4.8):
        """
        Array Response wrapper routine for MESS 2014.

        :param stream: Waveforms for the array processing.
        :type stream: :class:`obspy.core.stream.Stream`
        :param slx: Min/Max slowness for analysis in x direction.
        :type slx: (float, float)
        :param sly: Min/Max slowness for analysis in y direction.
        :type sly: (float, float)
        :param sls: step width of slowness grid
        :type sls: float
        :param frqmin: Low corner of frequency range for array analysis
        :type frqmin: float
        :param frqmax: High corner of frequency range for array analysis
        :type frqmax: float
        :param numfreqs: number of frequency values used for computing array
         transfer function
        :type numfreqs: int
        :param coordsys: defined coordinate system of stations (lonlat or km)
        :type coordsys: string
        :param correct_3dplane: correct for an inclined surface (not used)
        :type correct_3dplane: bool
        :param static_3D: correct topography
        :type static_3D: bool
        :param velcor: velocity used for static_3D correction
        :type velcor: float
        """
        self._attach_coords_to_stream(stream)

        sllx, slmx = sx
        slly, slmy = sx
        sllx /= KM_PER_DEG
        slmx /= KM_PER_DEG
        slly /= KM_PER_DEG
        slmy /= KM_PER_DEG
        sls = sls/KM_PER_DEG

        stepsfreq = (freqmax - freqmin) / float(numfreqs)
        transff = array_transff_freqslowness(
            stream, (sllx, slmx, slly, slmy), sls, freqmin, freqmax, stepsfreq,
            coordsys=coordsys, correct_3dplane=False, static_3D=static3D,
            vel_cor=velcor)

        sllx *= KM_PER_DEG
        slmx *= KM_PER_DEG
        slly *= KM_PER_DEG
        slmy *= KM_PER_DEG
        sls *= KM_PER_DEG

        slx = np.arange(sllx, slmx+sls, sls)
        sly = np.arange(slly, slmy+sls, sls)
        fig = plt.figure(figsize=(12, 12))
        ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])

        #ax.pcolormesh(slx, sly, transff.T)
        ax.contour(sly, slx, transff.T, 10)
        ax.set_xlabel('slowness [s/deg]')
        ax.set_ylabel('slowness [s/deg]')
        ax.set_ylim(slx[0], slx[-1])
        ax.set_xlim(sly[0], sly[-1])
        plt.show()

    def _attach_coords_to_stream(self, stream):
        """
        Attaches dictionary with latitude, longitude and elevation to each
        trace in stream as `trace.stats.coords`. Takes into account local depth of sensor.
        """
        geo = self.geometry

        for tr in stream:
            station_code = "{n}.{s}".format(n=tr.stats.network,
                                            s=tr.stats.station)
            coords = geo[station_code]
            z = coords["elevation_in_m"] - coords["local_depth_in_m"]
            tr.stats.coordinates = \
                AttribDict(dict(latitude=coords["latitude"],
                                longitude=coords["longitude"],
                                elevation=z))


def array_rotation_strain(subarray, ts1, ts2, ts3, vp, vs, array_coords,
                          sigmau):
    """
    This routine calculates the best-fitting rigid body rotation and
    uniform strain as functions of time, and their formal errors, given
    three-component ground motion time series recorded on a seismic array.
    The theory implemented herein is presented in the papers [Spudich1995]_,
    (abbreviated S95 herein) [Spudich2008]_ (SF08) and [Spudich2009]_ (SF09).

    This is a translation of the Matlab Code presented in (SF09) with
    small changes in details only. Output has been checked to be the same
    as the original Matlab Code.

    .. note::
        ts\_ below means "time series"

    :type vp: float
    :param vp: P wave speed in the soil under the array (km/s)
    :type vs: float
    :param vs: S wave speed in the soil under the array Note - vp and vs may be
        any unit (e.g. miles/week), and this unit need not be related to the
        units of the station coordinates or ground motions, but the units of vp
        and vs must be the SAME because only their ratio is used.
    :type array_coords: numpy.ndarray
    :param array_coords: array of dimension Na x 3, where Na is the number of
        stations in the array.  array_coords[i,j], i in arange(Na), j in
        arange(3) is j coordinate of station i.  units of array_coords may be
        anything, but see the "Discussion of input and output units" above.
        The origin of coordinates is arbitrary and does not affect the
        calculated strains and rotations.  Stations may be entered in any
        order.
    :type ts1: numpy.ndarray
    :param ts1: array of x1-component seismograms, dimension nt x Na.
        ts1[j,k], j in arange(nt), k in arange(Na) contains the k'th time
        sample of the x1 component ground motion at station k. NOTE that the
        seismogram in column k must correspond to the station whose coordinates
        are in row k of in.array_coords. nt is the number of time samples in
        the seismograms.  Seismograms may be displacement, velocity,
        acceleration, jerk, etc.  See the "Discussion of input and output
        units" below.
    :type ts2: numpy.ndarray
    :param ts2: same as ts1, but for the x2 component of motion.
    :type ts3: numpy.ndarray
    :param ts3: same as ts1, but for the x3 (UP or DOWN) component of motion.
    :type sigmau: float or :class:`numpy.ndarray`
    :param sigmau: standard deviation (NOT VARIANCE) of ground noise,
        corresponds to sigma-sub-u in S95 lines above eqn (A5).
        NOTE: This may be entered as a scalar, vector, or matrix!

        * If sigmau is a scalar, it will be used for all components of all
          stations.
        * If sigmau is a 1D array of length Na, sigmau[i] will be the noise
          assigned to all components of the station corresponding to
          array_coords[i,:]
        * If sigmau is a 2D array of dimension  Na x 3, then sigmau[i,j] is
          used as the noise of station i, component j.

        In all cases, this routine assumes that the noise covariance between
        different stations and/or components is zero.
    :type subarray: numpy.ndarray
    :param subarray: NumPy array of subarray stations to use. I.e. if subarray
        = array([1, 4, 10]), then only rows 1, 4, and 10 of array_coords will
        be used, and only ground motion time series in the first, fourth, and
        tenth columns of ts1 will be used. n_plus_1 is the number of elements
        in the subarray vector, and N is set to n_plus_1 - 1. To use all
        stations in the array, set in.subarray = arange(Na), where Na is the
        total number of stations in the array (equal to the number of rows of
        in.array_coords. Sequence of stations in the subarray vector is
        unimportant; i.e.  subarray = array([1, 4, 10]) will yield essentially
        the same rotations and strains as subarray = array([10, 4, 1]).
        "Essentially" because permuting subarray sequence changes the d vector,
        yielding a slightly different numerical result.
    :return: Dictionary with fields:

        **A:** (array, dimension 3N x 6)
            data mapping matrix 'A' of S95(A4)
        **g:** (array, dimension 6 x 3N)
            generalized inverse matrix relating ptilde and data vector, in
            S95(A5)
        **Ce:** (4 x 4)
            covariance matrix of the 4 independent strain tensor elements e11,
            e21, e22, e33
        **ts_d:** (array, length nt)
            dilatation (trace of the 3x3 strain tensor) as a function of time
        **sigmad:** (scalar)
            standard deviation of dilatation
        **ts_dh:** (array, length nt)
            horizontal dilatation (also known as areal strain) (eEE+eNN) as a
            function of time
        **sigmadh:** (scalar)
            standard deviation of horizontal dilatation (areal strain)
        **ts_e:** (array, dimension nt x 3 x 3)
            strain tensor
        **ts_s:** (array, length nt)
            maximum strain ( .5*(max eigval of e - min eigval of e) as a
            function of time, where e is the 3x3 strain tensor
        **Cgamma:** (4 x 4)
            covariance matrix of the 4 independent shear strain tensor elements
            g11, g12, g22, g33 (includes full covariance effects). gamma is
            traceless part of e.
        **ts_sh:** (array, length nt)
            maximum horizontal strain ( .5*(max eigval of eh - min eigval of
            eh) as a function of time, where eh is e(1:2,1:2)
        **Cgammah:** (3 x 3)
            covariance matrix of the 3 independent horizontal shear strain
            tensor elements gamma11, gamma12, gamma22 gamma is traceless part
            of e.
        **ts_wmag:** (array, length nt)
            total rotation angle (radians) as a function of time.  I.e. if the
            rotation vector at the j'th time step is
            w = array([w1, w2, w3]), then ts_wmag[j] = sqrt(sum(w**2))
            positive for right-handed rotation
        **Cw:** (3 x 3)
            covariance matrix of the 3 independent rotation tensor elements
            w21, w31, w32
        **ts_w1:** (array, length nt)
            rotation (rad) about the x1 axis, positive for right-handed
            rotation
        **sigmaw1:** (scalar)
            standard deviation of the ts_w1 (sigma-omega-1 in SF08)
        **ts_w2:** (array, length nt)
            rotation (rad) about the x2 axis, positive for right-handed
            rotation
        **sigmaw2:** (scalar)
            standard deviation of ts_w2 (sigma-omega-2 in SF08)
        **ts_w3:** (array, length nt)
            "torsion", rotation (rad) about a vertical up or down axis, i.e.
            x3, positive for right-handed rotation
        **sigmaw3:** (scalar)
            standard deviation of the torsion (sigma-omega-3 in SF08)
        **ts_tilt:** (array, length nt)
            tilt (rad) (rotation about a horizontal axis, positive for right
            handed rotation) as a function of time
            tilt = sqrt( w1^2 + w2^2)
        **sigmat:** (scalar)
            standard deviation of the tilt (not defined in SF08, From
            Papoulis (1965, p. 195, example 7.8))
        **ts_data:** (array, shape (nt x 3N))
            time series of the observed displacement differences, which are
            the di in S95 eqn A1
        **ts_pred:** (array, shape (nt x 3N))
            time series of the fitted model's predicted displacement difference
            Note that the fitted model displacement differences correspond
            to linalg.dot(A, ptilde), where A is the big matrix in S95 eqn A4
            and ptilde is S95 eqn A5
        **ts_misfit:** (array, shape (nt x 3N))
            time series of the residuals (fitted model displacement differences
            minus observed displacement differences). Note that the fitted
            model displacement differences correspond to linalg.dot(A, ptilde),
            where A is the big matrix in S95 eqn A4 and ptilde is S95 eqn A5
        **ts_M:** (array, length nt)
            Time series of M, misfit ratio of S95, p. 688
        **ts_ptilde:** (array, shape (nt x 6))
            solution vector p-tilde (from S95 eqn A5) as a function of time
        **Cp:** (6 x 6)
            solution covariance matrix defined in SF08

    .. rubric:: Warnings

    This routine does not check to verify that your array is small
    enough to conform to the assumption that the array aperture is less
    than 1/4 of the shortest seismic wavelength in the data. See SF08
    for a discussion of this assumption.

    This code assumes that ts1[j,:], ts2[j,:], and ts3[j,:] are all sampled
    SIMULTANEOUSLY.

    .. rubric:: Notes

    (1) Note On Specifying Input Array And Selecting Subarrays

        This routine allows the user to input the coordinates and ground
        motion time series of all stations in a seismic array having Na
        stations and the user may select for analysis a subarray of n_plus_1
        <= Na stations.

    (2) Discussion Of Physical Units Of Input And Output

        If the input seismograms are in units of displacement, the output
        strains and rotations will be in units of strain (unitless) and
        angle (radians).  If the input seismograms are in units of
        velocity, the output will be strain rate (units = 1/s) and rotation
        rate (rad/s).  Higher temporal derivative inputs yield higher
        temporal derivative outputs.

        Input units of the array station coordinates must match the spatial
        units of the seismograms.  For example, if the input seismograms
        are in units of m/s^2, array coordinates must be entered in m.

    (3) Note On Coordinate System

        This routine assumes x1-x2-x3 is a RIGHT handed orthogonal
        coordinate system. x3 must point either UP or DOWN.
    """
    # This assumes that all stations and components have the same number of
    # time samples, nt
    [nt, na] = np.shape(ts1)

    # check to ensure all components have same duration
    if ts1.shape != ts2.shape:
        raise ValueError('ts1 and ts2 have different sizes')
    if ts1.shape != ts3.shape:
        raise ValueError('ts1 and ts3 have different sizes')

    # check to verify that the number of stations in ts1 agrees with the number
    # of stations in array_coords
    nrac, _ = array_coords.shape
    if nrac != na:
        msg = 'ts1 has %s columns(stations) but array_coords has ' % na + \
              '%s rows(stations)' % nrac
        raise ValueError(msg)

    # check stations in subarray exist
    if min(subarray) < 0:
        raise ValueError('Station number < 0 in subarray')
    if max(subarray) > na:
        raise ValueError('Station number > Na in subarray')

    # extract the stations of the subarray to be used
    subarraycoords = array_coords[subarray, :]

    # count number of subarray stations: n_plus_1 and number of station
    # offsets: N
    n_plus_1 = subarray.size
    N = n_plus_1 - 1

    if n_plus_1 < 3:
        msg = 'The problem is underdetermined for fewer than 3 stations'
        raise ValueError(msg)
    elif n_plus_1 == 3:
        msg = 'For a 3-station array the problem is even-determined'
        warnings.warn(msg)

    # ------------------- NOW SOME SEISMOLOGY!! --------------------------
    # constants
    eta = 1 - 2 * vs ** 2 / vp ** 2

    # form A matrix, which relates model vector of 6 displacement derivatives
    # to vector of observed displacement differences. S95(A3)
    # dim(A) = (3*N) * 6
    # model vector is [ u1,1 u1,2 u1,3 u2,1 u2,2 u2,3 ] (free surface boundary
    # conditions applied, S95(A2))
    # first initialize A to the null matrix
    A = np.zeros((N * 3, 6))
    z3t = np.zeros(3)
    # fill up A
    for i in range(N):
        ss = subarraycoords[(i + 1), :] - subarraycoords[0, :]
        A[(3 * i):(3 * i + 3), :] = np.c_[
            np.r_[ss, z3t], np.r_[z3t, ss],
            np.array([-eta * ss[2],
                     0., -ss[0], 0., -eta * ss[2], -ss[1]])].transpose()

    # ------------------------------------------------------
    # define data covariance matrix Cd.
    # step 1 - define data differencing matrix D
    # dimension of D is (3*N) * (3*n_plus_1)
    I3 = np.eye(3)
    II = np.eye(3 * N)
    D = -I3

    for i in range(N - 1):
        D = np.c_[D, -I3]
    D = np.r_[D, II].T

    # step 2 - define displacement u covariance matrix Cu
    # This assembles a covariance matrix Cu that reflects actual data errors.
    # populate Cu depending on the size of sigmau
    if np.size(sigmau) == 1:
        # sigmau is a scalar.  Make all diag elements of Cu the same
        Cu = sigmau ** 2 * np.eye(3 * n_plus_1)
    elif np.shape(sigmau) == (np.size(sigmau),):
        # sigmau is a row or column vector
        # check dimension is okay
        if np.size(sigmau) != na:
            raise ValueError('sigmau must have %s elements' % na)
        junk = (np.c_[sigmau, sigmau, sigmau]) ** 2  # matrix of variances
        Cu = np.diag(np.reshape(junk[subarray, :], (3 * n_plus_1)))
    elif sigmau.shape == (na, 3):
        Cu = np.diag(np.reshape(((sigmau[subarray, :]) ** 2).transpose(),
                     (3 * n_plus_1)))
    else:
        raise ValueError('sigmau has the wrong dimensions')

    # Cd is the covariance matrix of the displ differences
    # dim(Cd) is (3*N) * (3*N)
    Cd = np.dot(np.dot(D, Cu), D.T)

    # ---------------------------------------------------------
    # form generalized inverse matrix g.  dim(g) is 6 x (3*N)
    Cdi = np.linalg.inv(Cd)
    AtCdiA = np.dot(np.dot(A.T, Cdi), A)
    g = np.dot(np.dot(np.linalg.inv(AtCdiA), A.T), Cdi)

    condition_number = np.linalg.cond(AtCdiA)

    if condition_number > 100:
        msg = 'Condition number is %s' % condition_number
        warnings.warn(msg)

    # set up storage for vectors that will contain time series
    ts_wmag = np.empty(nt)
    ts_w1 = np.empty(nt)
    ts_w2 = np.empty(nt)
    ts_w3 = np.empty(nt)
    ts_tilt = np.empty(nt)
    ts_dh = np.empty(nt)
    ts_sh = np.empty(nt)
    ts_s = np.empty(nt)
    ts_pred = np.empty((nt, 3 * N))
    ts_misfit = np.empty((nt, 3 * N))
    ts_M = np.empty(nt)
    ts_data = np.empty((nt, 3 * N))
    ts_ptilde = np.empty((nt, 6))
    for array in (ts_wmag, ts_w1, ts_w2, ts_w3, ts_tilt, ts_dh, ts_sh, ts_s,
                  ts_pred, ts_misfit, ts_M, ts_data, ts_ptilde):
        array.fill(np.NaN)
    ts_e = np.empty((nt, 3, 3))
    ts_e.fill(np.NaN)

    # other matrices
    udif = np.empty((3, N))
    udif.fill(np.NaN)

    # ---------------------------------------------------------------
    # here we define 4x6 Be and 3x6 Bw matrices.  these map the solution
    # ptilde to strain or to rotation.  These matrices will be used
    # in the calculation of the covariances of strain and rotation.
    # Columns of both matrices correspond to the model solution vector
    # containing elements [u1,1 u1,2 u1,3 u2,1 u2,2 u2,3 ]'
    #
    # the rows of Be correspond to e11 e21 e22 and e33
    Be = np.zeros((4, 6))
    Be[0, 0] = 2.
    Be[1, 1] = 1.
    Be[1, 3] = 1.
    Be[2, 4] = 2.
    Be[3, 0] = -2 * eta
    Be[3, 4] = -2 * eta
    Be = Be * .5
    #
    # the rows of Bw correspond to w21 w31 and w32
    Bw = np.zeros((3, 6))
    Bw[0, 1] = 1.
    Bw[0, 3] = -1.
    Bw[1, 2] = 2.
    Bw[2, 5] = 2.
    Bw = Bw * .5
    #
    # this is the 4x6 matrix mapping solution to total shear strain gamma
    # where gamma = strain - tr(strain)/3 * eye(3)
    # the four elements of shear are 11, 12, 22, and 33.  It is symmetric.
    aa = (2 + eta) / 3
    b = (1 - eta) / 3
    c = (1 + 2 * eta) / 3
    Bgamma = np.zeros((4, 6))
    Bgamma[0, 0] = aa
    Bgamma[0, 4] = -b
    Bgamma[2, 2] = .5
    Bgamma[1, 3] = .5
    Bgamma[2, 0] = -b
    Bgamma[2, 4] = aa
    Bgamma[3, 0] = -c
    Bgamma[3, 4] = -c
    #
    # this is the 3x6 matrix mapping solution to horizontal shear strain
    # gamma
    # the four elements of horiz shear are 11, 12, and 22.  It is symmetric.
    Bgammah = np.zeros((3, 6))
    Bgammah[0, 0] = .5
    Bgammah[0, 4] = -.5
    Bgammah[1, 1] = .5
    Bgammah[1, 3] = .5
    Bgammah[2, 0] = -.5
    Bgammah[2, 4] = .5

    # solution covariance matrix.  dim(Cp) = 6 * 6
    # corresponding to solution elements [u1,1 u1,2 u1,3 u2,1 u2,2 u2,3 ]
    Cp = np.dot(np.dot(g, Cd), g.T)

    # Covariance of strain tensor elements
    # Ce should be 4x4, correspond to e11, e21, e22, e33
    Ce = np.dot(np.dot(Be, Cp), Be.T)
    # Cw should be 3x3 correspond to w21, w31, w32
    Cw = np.dot(np.dot(Bw, Cp), Bw.T)

    # Cgamma is 4x4 correspond to 11, 12, 22, and 33.
    Cgamma = np.dot(np.dot(Bgamma, Cp), Bgamma.T)
    #
    #  Cgammah is 3x3 correspond to 11, 12, and 22
    Cgammah = np.dot(np.dot(Bgammah, Cp), Bgammah.T)
    #
    #
    # covariance of the horizontal dilatation and the total dilatation
    # both are 1x1, i.e. scalars
    Cdh = Cp[0, 0] + 2 * Cp[0, 4] + Cp[4, 4]
    sigmadh = np.sqrt(Cdh)

    # covariance of the (total) dilatation, ts_dd
    sigmadsq = (1 - eta) ** 2 * Cdh
    sigmad = np.sqrt(sigmadsq)
    #
    # Cw3, covariance of w3 rotation, i.e. torsion, is 1x1, i.e. scalar
    Cw3 = (Cp[1, 1] - 2 * Cp[1, 3] + Cp[3, 3]) / 4
    sigmaw3 = np.sqrt(Cw3)

    # For tilt cannot use same approach because tilt is not a linear function
    # of the solution.  Here is an approximation :
    # For tilt use conservative estimate from
    # Papoulis (1965, p. 195, example 7.8)
    sigmaw1 = np.sqrt(Cp[5, 5])
    sigmaw2 = np.sqrt(Cp[2, 2])
    sigmat = max(sigmaw1, sigmaw2) * np.sqrt(2 - np.pi / 2)

    #
    # BEGIN LOOP OVER DATA POINTS IN TIME SERIES==============================
    #
    for itime in range(nt):
        #
        # data vector is differences of stn i displ from stn 1 displ
        # sum the lengths of the displ difference vectors
        sumlen = 0
        for i in range(N):
            udif[0, i] = ts1[itime, subarray[i + 1]] - ts1[itime, subarray[0]]
            udif[1, i] = ts2[itime, subarray[i + 1]] - ts2[itime, subarray[0]]
            udif[2, i] = ts3[itime, subarray[i + 1]] - ts3[itime, subarray[0]]
            sumlen = sumlen + np.sqrt(np.sum(udif[:, i].T ** 2))

        data = udif.T.reshape(udif.size)
        #
        # form solution
        # ptilde is (u1,1 u1,2 u1,3 u2,1 u2,2 u2,3).T
        ptilde = np.dot(g, data)
        #
        # place in uij_vector the full 9 elements of the displacement gradients
        # uij_vector is (u1,1 u1,2 u1,3 u2,1 u2,2 u2,3 u3,1 u3,2 u3,3).T
        # The following implements the free surface boundary condition
        u31 = -ptilde[2]
        u32 = -ptilde[5]
        u33 = -eta * (ptilde[0] + ptilde[4])
        uij_vector = np.r_[ptilde, u31, u32, u33]
        #
        # calculate predicted data
        pred = np.dot(A, ptilde)  # 9/8/92.I.3(9) and 8/26/92.I.3.T bottom
        #
        # calculate  residuals (misfits concatenated for all stations)
        misfit = pred - data

        # Calculate ts_M, misfit ratio.
        # calculate summed length of misfits (residual displacements)
        misfit_sq = misfit ** 2
        misfit_sq = np.reshape(misfit_sq, (N, 3)).T
        misfit_sumsq = np.empty(N)
        misfit_sumsq.fill(np.NaN)
        for i in range(N):
            misfit_sumsq[i] = misfit_sq[:, i].sum()
        misfit_len = np.sum(np.sqrt(misfit_sumsq))
        ts_M[itime] = misfit_len / sumlen
        #
        ts_data[itime, 0:3 * N] = data.T
        ts_pred[itime, 0:3 * N] = pred.T
        ts_misfit[itime, 0:3 * N] = misfit.T
        ts_ptilde[itime, :] = ptilde.T
        #
        # ---------------------------------------------------------------
        # populate the displacement gradient matrix U
        U = np.zeros(9)
        U[:] = uij_vector
        U = U.reshape((3, 3))
        #
        # calculate strain tensors
        # Fung eqn 5.1 p 97 gives dui = (eij-wij)*dxj
        e = .5 * (U + U.T)
        ts_e[itime] = e

        # Three components of the rotation vector omega (=w here)
        w = np.empty(3)
        w.fill(np.NaN)
        w[0] = -ptilde[5]
        w[1] = ptilde[2]
        w[2] = .5 * (ptilde[3] - ptilde[1])

        # amount of total rotation is length of rotation vector
        ts_wmag[itime] = np.sqrt(np.sum(w ** 2))
        #
        # Calculate tilt and torsion
        ts_w1[itime] = w[0]
        ts_w2[itime] = w[1]
        ts_w3[itime] = w[2]  # torsion in radians
        ts_tilt[itime] = np.sqrt(w[0] ** 2 + w[1] ** 2)
        # 7/21/06.II.6(19), amount of tilt in radians

        # ---------------------------------------------------------------
        #
        # Here I calculate horizontal quantities only
        # ts_dh is horizontal dilatation (+ --> expansion).
        # Total dilatation, ts_dd, will be calculated outside the time
        # step loop.
        #
        ts_dh[itime] = e[0, 0] + e[1, 1]
        #
        # find maximum shear strain in horizontal plane, and find its azimuth
        eh = np.r_[np.c_[e[0, 0], e[0, 1]], np.c_[e[1, 0], e[1, 1]]]
        # 7/21/06.II.2(4)
        gammah = eh - np.trace(eh) * np.eye(2) / 2.
        # 9/14/92.II.4, 7/21/06.II.2(5)

        # eigvecs are principal axes, eigvals are principal strains
        [eigvals, _eigvecs] = np.linalg.eig(gammah)
        # max shear strain, from Fung (1965, p71, eqn (8)
        ts_sh[itime] = .5 * (max(eigvals) - min(eigvals))

        # calculate max of total shear strain, not just horizontal strain
        # eigvecs are principal axes, eigvals are principal strains
        [eigvalt, _eigvect] = np.linalg.eig(e)
        # max shear strain, from Fung (1965, p71, eqn (8)
        ts_s[itime] = .5 * (max(eigvalt) - min(eigvalt))
        #

    # =========================================================================
    #
    # (total) dilatation is a scalar times horizontal dilatation owing to there
    # free surface boundary condition
    ts_d = ts_dh * (1 - eta)

    # load output structure
    out = dict()

    out['A'] = A
    out['g'] = g
    out['Ce'] = Ce

    out['ts_d'] = ts_d
    out['sigmad'] = sigmad

    out['ts_dh'] = ts_dh
    out['sigmadh'] = sigmadh

    out['ts_s'] = ts_s
    out['Cgamma'] = Cgamma

    out['ts_sh'] = ts_sh
    out['Cgammah'] = Cgammah

    out['ts_wmag'] = ts_wmag
    out['Cw'] = Cw

    out['ts_w1'] = ts_w1
    out['sigmaw1'] = sigmaw1
    out['ts_w2'] = ts_w2
    out['sigmaw2'] = sigmaw2
    out['ts_w3'] = ts_w3
    out['sigmaw3'] = sigmaw3

    out['ts_tilt'] = ts_tilt
    out['sigmat'] = sigmat

    out['ts_data'] = ts_data
    out['ts_pred'] = ts_pred
    out['ts_misfit'] = ts_misfit
    out['ts_M'] = ts_M
    out['ts_e'] = ts_e

    out['ts_ptilde'] = ts_ptilde
    out['Cp'] = Cp

    out['ts_M'] = ts_M

    return out


def get_geometry(stream, coordsys='lonlat', return_center=False,
                 correct_3dplane=False, verbose=False):
    """
    Method to calculate the array geometry and the center coordinates in km

    :param stream: Stream object, the trace.stats dict like class must
        contain an :class:`~obspy.core.util.attribdict.AttribDict` with
        'latitude', 'longitude' (in degrees) and 'elevation' (in km), or 'x',
        'y', 'elevation' (in km) items/attributes. See param ``coordsys``
    :param coordsys: valid values: 'lonlat' and 'xy', choose which stream
        attributes to use for coordinates
    :param return_center: Retruns the center coordinates as extra tuple
    :param correct_3dplane: applies a 3D best fitting plane to the array.
           This might be important if the array is located on a inclinde slope
           (e.g., at a volcano)
    :return: Returns the geometry of the stations as 2d numpy.ndarray
            The first dimension are the station indexes with the same order
            as the traces in the stream object. The second index are the
            values of [lat, lon, elev] in km
            last index contains center [lat, lon, elev] in degrees and km if
            return_center is true
    """
    nstat = len(stream)
    center_lat = 0.
    center_lon = 0.
    center_h = 0.
    geometry = np.empty((nstat, 3))

    if isinstance(stream, Stream):
        for i, tr in enumerate(stream):
            if coordsys == 'lonlat':
                geometry[i, 0] = tr.stats.coordinates.longitude
                geometry[i, 1] = tr.stats.coordinates.latitude
                geometry[i, 2] = tr.stats.coordinates.elevation/1000.
            elif coordsys == 'xy':
                geometry[i, 0] = tr.stats.coordinates.x
                geometry[i, 1] = tr.stats.coordinates.y
                geometry[i, 2] = tr.stats.coordinates.elevation/1000.
    elif isinstance(stream, np.ndarray):
        geometry = stream.copy()
    else:
        raise TypeError('only Stream or numpy.ndarray allowed')

    if verbose:
        print(("coordys = " + coordsys))

    if coordsys == 'lonlat':
        center_lon = geometry[:, 0].mean()
        center_lat = geometry[:, 1].mean()
        center_h = geometry[:, 2].mean()
        for i in np.arange(nstat):
            x, y = utlGeoKm(center_lon, center_lat, geometry[i, 0],
                            geometry[i, 1])
            geometry[i, 0] = x
            geometry[i, 1] = y
            geometry[i, 2] -= center_h
    elif coordsys == 'xy':
        geometry[:, 0] -= geometry[:, 0].mean()
        geometry[:, 1] -= geometry[:, 1].mean()
        geometry[:, 2] -= geometry[:, 2].mean()
    else:
        raise ValueError("Coordsys must be one of 'lonlat', 'xy'")

    print("Center of Gravity: ", center_lon, " ", center_lat, " ", center_h)

    if correct_3dplane:
        A = geometry
        u, s, vh = np.linalg.linalg.svd(A)
        v = vh.conj().transpose()
        # satisfies the plane equation a*x + b*y + c*z = 0
        result = np.zeros((nstat, 3))
        # now we are seeking the station positions on that plane
        # geometry[:,2] += v[2,-1]
        n = v[:, -1]
        result[:, 0] = (geometry[:, 0] - n[0] * (
            n[0] * geometry[:, 0] + geometry[:, 1] * n[1] + n[2] *
            geometry[:, 2]) / (
                n[0] * n[0] + n[1] * n[1] + n[2] * n[2]))
        result[:, 1] = (geometry[:, 1] - n[1] * (
            n[0] * geometry[:, 0] + geometry[:, 1] * n[1] + n[2] *
            geometry[:, 2]) / (
                n[0] * n[0] + n[1] * n[1] + n[2] * n[2]))
        result[:, 2] = (geometry[:, 2] - n[2] * (
            n[0] * geometry[:, 0] + geometry[:, 1] * n[1] + n[2] *
            geometry[:, 2]) / (
                n[0] * n[0] + n[1] * n[1] + n[2] * n[2]))
        geometry = result[:]
        print("Best fitting plane-coordinates :", geometry)

    if return_center:
        return np.c_[geometry.T,
                     np.array((center_lon, center_lat, center_h))].T
    else:
        return geometry


def get_stream_offsets(stream, stime, etime):
    """
    Calculates start and end offsets relative to stime and etime for each
    trace in stream in samples.

    :type stime: :class:`~obspy.core.utcdatetime.UTCDateTime`
    :param stime: Start time
    :type etime: :class:`~obspy.core.utcdatetime.UTCDateTime`
    :param etime: End time
    :returns: start and end sample offset arrays
    """
    slatest = stream[0].stats.starttime
    eearliest = stream[0].stats.endtime
    for tr in stream:
        if tr.stats.starttime >= slatest:
            slatest = tr.stats.starttime
        if tr.stats.endtime <= eearliest:
            eearliest = tr.stats.endtime

    nostat = len(stream)
    spoint = np.empty(nostat, dtype=np.int32, order="C")
    epoint = np.empty(nostat, dtype=np.int32, order="C")
    # now we have to adjust to the beginning of real start time
    if (slatest - stime) > stream[0].stats.delta / 2.:
        msg = "Specified start-time is smaller than starttime in stream"
        raise ValueError(msg)
    if (eearliest - etime) < -stream[0].stats.delta/2.:
        msg = "Specified end-time bigger is than endtime in stream"
        print(eearliest, etime)
        raise ValueError(msg)
    for i in xrange(nostat):
        offset = int(((stime - slatest) / stream[i].stats.delta + 1.))
        negoffset = int(((eearliest - etime) / stream[i].stats.delta + 1.))
        diffstart = slatest - stream[i].stats.starttime
        frac, ddummy = math.modf(diffstart)
        spoint[i] = int(ddummy)
        if frac > stream[i].stats.delta * 0.25:
            msg = "Difference in start times exceeds 25% of samp rate"
            warnings.warn(msg)
        spoint[i] += offset
        diffend = stream[i].stats.endtime - eearliest
        frac, ddummy = math.modf(diffend)
        epoint[i] = int(ddummy)
        epoint[i] += negoffset
    return spoint, epoint


def array_transff_wavenumber(coords, klim, kstep, coordsys='lonlat'):
    """
    Returns array transfer function as a function of wavenumber difference

    :type coords: numpy.ndarray
    :param coords: coordinates of stations in longitude and latitude in degrees
        elevation in km, or x, y, z in km
    :type coordsys: str
    :param coordsys: valid values: 'lonlat' and 'xy', choose which coordinates
        to use
    :param klim: either a float to use symmetric limits for wavenumber
        differences or the tuple (kxmin, kxmax, kymin, kymax)
    """
    coords = get_geometry(coords, coordsys)
    if isinstance(klim, float):
        kxmin = -klim
        kxmax = klim
        kymin = -klim
        kymax = klim
    elif isinstance(klim, tuple):
        if len(klim) == 4:
            kxmin = klim[0]
            kxmax = klim[1]
            kymin = klim[2]
            kymax = klim[3]
    else:
        raise TypeError('klim must either be a float or a tuple of length 4')

    nkx = int(np.ceil((kxmax + kstep / 10. - kxmin) / kstep))
    nky = int(np.ceil((kymax + kstep / 10. - kymin) / kstep))

    transff = np.empty((nkx, nky))

    for i, kx in enumerate(np.arange(kxmin, kxmax + kstep / 10., kstep)):
        for j, ky in enumerate(np.arange(kymin, kymax + kstep / 10., kstep)):
            _sum = 0j
            for k in range(len(coords)):
                _sum += np.exp(complex(0.,
                               coords[k, 0] * kx + coords[k, 1] * ky))
            transff[i, j] = abs(_sum) ** 2

    transff /= transff.max()
    return transff


def array_transff_freqslowness(stream, slim, sstep, fmin, fmax, fstep,
                               coordsys='lonlat', correct_3dplane=False,
                               static_3D=False, vel_cor=4.):
    """
    Returns array transfer function as a function of slowness difference and
    frequency.

    :type coords: numpy.ndarray
    :param coords: coordinates of stations in longitude and latitude in degrees
        elevation in km, or x, y, z in km
    :type coordsys: str
    :param coordsys: valid values: 'lonlat' and 'xy', choose which coordinates
        to use
    :param slim: either a float to use symmetric limits for slowness
        differences or the tupel (sxmin, sxmax, symin, symax)
    :type fmin: float
    :param fmin: minimum frequency in signal
    :type fmax: float
    :param fmin: maximum frequency in signal
    :type fstep: float
    :param fmin: frequency sample distance
    """
    geometry = get_geometry(stream, coordsys=coordsys,
                            correct_3dplane=correct_3dplane, verbose=False)

    if isinstance(slim, float):
        sxmin = -slim
        sxmax = slim
        symin = -slim
        symax = slim
    elif isinstance(slim, tuple):
        if len(slim) == 4:
            sxmin = slim[0]
            sxmax = slim[1]
            symin = slim[2]
            symax = slim[3]
    else:
        raise TypeError('slim must either be a float or a tuple of length 4')

    nsx = int(np.ceil((sxmax + sstep / 10. - sxmin) / sstep))
    nsy = int(np.ceil((symax + sstep / 10. - symin) / sstep))
    nf = int(np.ceil((fmax + fstep / 10. - fmin) / fstep))

    transff = np.empty((nsx, nsy))
    buff = np.zeros(nf)

    for i, sx in enumerate(np.arange(sxmin, sxmax + sstep / 10., sstep)):
        for j, sy in enumerate(np.arange(symin, symax + sstep / 10., sstep)):
            for k, f in enumerate(np.arange(fmin, fmax + fstep / 10., fstep)):
                _sum = 0j
                for l in np.arange(len(geometry)):
                    _sum += np.exp(complex(
                        0., (geometry[l, 0] * sx + geometry[l, 1] * sy) *
                        2 * np.pi * f))
                buff[k] = abs(_sum) ** 2
            transff[i, j] = cumtrapz(buff, dx=fstep)[-1]

    transff /= transff.max()
    return transff


def dump(pow_map, apow_map, i):
    """
    Example function to use with `store` kwarg in
    :func:`~obspy.signal.array_analysis.array_processing`.
    """
    np.save('pow_map_%d' % i, pow_map)
    np.save('apow_map_%d' % i, apow_map)


def array_processing(stream, win_len, win_frac, sll_x, slm_x, sll_y, slm_y,
                     sl_s, semb_thres, vel_thres, frqlow, frqhigh, stime,
                     etime, prewhiten, verbose=False, coordsys='lonlat',
                     timestamp='mlabday', method=0, correct_3dplane=False,
                     vel_cor=4., static_3D=False, store=None):
    """
    Method for FK-Analysis/Capon

    :param stream: Stream object, the trace.stats dict like class must
        contain an :class:`~obspy.core.util.attribdict.AttribDict` with
        'latitude', 'longitude' (in degrees) and 'elevation' (in km), or 'x',
        'y', 'elevation' (in km) items/attributes. See param ``coordsys``.
    :type win_len: float
    :param win_len: Sliding window length in seconds
    :type win_frac: float
    :param win_frac: Fraction of sliding window to use for step
    :type sll_x: float
    :param sll_x: slowness x min (lower)
    :type slm_x: float
    :param slm_x: slowness x max
    :type sll_y: float
    :param sll_y: slowness y min (lower)
    :type slm_y: float
    :param slm_y: slowness y max
    :type sl_s: float
    :param sl_s: slowness step
    :type semb_thres: float
    :param semb_thres: Threshold for semblance
    :type vel_thres: float
    :param vel_thres: Threshold for velocity
    :type frqlow: float
    :param frqlow: lower frequency for fk/capon
    :type frqhigh: float
    :param frqhigh: higher frequency for fk/capon
    :type stime: :class:`~obspy.core.utcdatetime.UTCDateTime`
    :param stime: Start time of interest
    :type etime: :class:`~obspy.core.utcdatetime.UTCDateTime`
    :param etime: End time of interest
    :type prewhiten: int
    :param prewhiten: Do prewhitening, values: 1 or 0
    :param coordsys: valid values: 'lonlat' and 'xy', choose which stream
        attributes to use for coordinates
    :type timestamp: str
    :param timestamp: valid values: 'julsec' and 'mlabday'; 'julsec' returns
        the timestamp in seconds since 1970-01-01T00:00:00, 'mlabday'
        returns the timestamp in days (decimals represent hours, minutes
        and seconds) since '0001-01-01T00:00:00' as needed for matplotlib
        date plotting (see e.g. matplotlib's num2date)
    :type method: int
    :param method: the method to use 0 == bf, 1 == capon
    :param vel_cor: correction velocity (upper layer) in km/s
    :param static_3D: a correction of the station height is applied using
        vel_cor the correction is done according to the formula:
        t = rxy*s - rz*cos(inc)/vel_cor
        where inc is defined by inv = asin(vel_cor*slow)
    :type store: function
    :param store: A custom function which gets called on each iteration. It is
        called with the relative power map and the time offset as first and
        second arguments and the iteration number as third argument. Useful for
        storing or plotting the map for each iteration. For this purpose the
        dump function of this module can be used.
    :return: :class:`numpy.ndarray` of timestamp, relative relpow, absolute
        relpow, backazimuth, slowness
    """
    BF, CAPON = 0, 1
    res = []
    eotr = True

    # check that sampling rates do not vary
    fs = stream[0].stats.sampling_rate
    if len(stream) != len(stream.select(sampling_rate=fs)):
        msg = ('in array-processing sampling rates of traces in stream are '
               'not equal')
        raise ValueError(msg)

    grdpts_x = int(((slm_x - sll_x) / sl_s + 0.5) + 1)
    grdpts_y = int(((slm_y - sll_y) / sl_s + 0.5) + 1)

    geometry = get_geometry(stream, coordsys=coordsys,
                            correct_3dplane=correct_3dplane, verbose=verbose)

    if verbose:
        print("geometry:")
        print(geometry)
        print("stream contains following traces:")
        print(stream)
        print(("stime = " + str(stime) + ", etime = " + str(etime)))

    time_shift_table = get_timeshift(geometry, sll_x, sll_y, sl_s, grdpts_x,
                                     grdpts_y, vel_cor=vel_cor,
                                     static_3D=static_3D)
    # offset of arrays
    mini = np.min(time_shift_table[:, :, :])
    maxi = np.max(time_shift_table[:, :, :])

    spoint, _epoint = get_stream_offsets(stream, stime, etime)

    # loop with a sliding window over the dat trace array and apply bbfk
    nstat = len(stream)
    fs = stream[0].stats.sampling_rate
    if win_len < 0.:
        nsamp = int((etime - stime)*fs)
        print(nsamp)
        nstep = 1
    else:
        nsamp = int(win_len * fs)
        nstep = int(nsamp * win_frac)

    # generate plan for rfftr
    nfft = nextpow2(nsamp)
    deltaf = fs / float(nfft)
    nlow = int(frqlow / float(deltaf) + 0.5)
    nhigh = int(frqhigh / float(deltaf) + 0.5)
    nlow = max(1, nlow)  # avoid using the offset
    nhigh = min(nfft // 2 - 1, nhigh)  # avoid using nyquist
    nf = nhigh - nlow + 1  # include upper and lower frequency

    # to spead up the routine a bit we estimate all steering vectors in advance
    steer = np.empty((nf, grdpts_x, grdpts_y, nstat), dtype=np.complex128)
    clibsignal.calcSteer(nstat, grdpts_x, grdpts_y, nf, nlow,
                         deltaf, time_shift_table, steer)
    R = np.empty((nf, nstat, nstat), dtype=np.complex128)
    ft = np.empty((nstat, nf), dtype=np.complex128)
    newstart = stime
    tap = cosTaper(nsamp, p=0.22)  # 0.22 matches 0.2 of historical C bbfk.c
    offset = 0
    count = 0
    relpow_map = np.empty((grdpts_x, grdpts_y), dtype=np.float64)
    abspow_map = np.empty((grdpts_x, grdpts_y), dtype=np.float64)
    while eotr:
        try:
            for i, tr in enumerate(stream):
                dat = tr.data[spoint[i] + offset:
                              spoint[i] + offset + nsamp]
                dat = (dat - dat.mean()) * tap
                ft[i, :] = np.fft.rfft(dat, nfft)[nlow:nlow + nf]
        except IndexError:
            break
        ft = np.ascontiguousarray(ft, np.complex128)
        relpow_map.fill(0.)
        abspow_map.fill(0.)
        # computing the covariances of the signal at different receivers
        dpow = 0.
        for i in range(nstat):
            for j in range(i, nstat):
                R[:, i, j] = ft[i, :] * ft[j, :].conj()
                if method == 1:
                    R[:, i, j] /= np.abs(R[:, i, j].sum())
                if i != j:
                    R[:, j, i] = R[:, i, j].conjugate()
                else:
                    dpow += np.abs(R[:, i, j].sum())
        dpow *= nstat
        if method == 1:
            # P(f) = 1/(e.H R(f)^-1 e)
            for n in range(nf):
                R[n, :, :] = np.linalg.pinv(R[n, :, :], rcond=1e-6)

        errcode = clibsignal.generalizedBeamformer(
            relpow_map, abspow_map, steer, R, nstat, prewhiten,
            grdpts_x, grdpts_y, nf, dpow, method)
        if errcode != 0:
            msg = 'generalizedBeamforming exited with error %d'
            raise Exception(msg % errcode)
        ix, iy = np.unravel_index(relpow_map.argmax(), relpow_map.shape)
        relpow, abspow = relpow_map[ix, iy], abspow_map[ix, iy]
        if store is not None:
            store(relpow_map, abspow_map, count)
        count += 1

        # here we compute baz, slow
        slow_x = sll_x + ix * sl_s
        slow_y = sll_y + iy * sl_s

        slow = np.sqrt(slow_x ** 2 + slow_y ** 2)
        if slow < 1e-8:
            slow = 1e-8
        azimut = 180 * math.atan2(slow_x, slow_y) / math.pi
        baz = azimut % -360 + 180
        if relpow > semb_thres and 1. / slow > vel_thres:
            res.append(np.array([newstart.timestamp, relpow, abspow, baz,
                                 slow]))
            if verbose:
                print((newstart, (newstart + (nsamp / fs)), res[-1][1:]))
        if (newstart + (nsamp + nstep) / fs) > etime:
            eotr = False
        offset += nstep

        newstart += nstep / fs
    res = np.array(res)
    if timestamp == 'julsec':
        pass
    elif timestamp == 'mlabday':
        # 719162 == hours between 1970 and 0001
        res[:, 0] = res[:, 0] / (24. * 3600) + 719162
    else:
        msg = "Option timestamp must be one of 'julsec', or 'mlabday'"
        raise ValueError(msg)
    return np.array(res)


def beamforming(stream, sll_x, slm_x, sll_y, slm_y, sl_s, frqlow, frqhigh,
                stime, etime,   win_len=-1, win_frac=0.5,
                verbose=False, coordsys='lonlat', timestamp='mlabday',
                method="DLS", nthroot=1, store=None, correct_3dplane=False,
                static_3D=False, vel_cor=4.):
    """
    Method for Delay and Sum/Phase Weighted Stack/Whitened Slowness Power

    :param stream: Stream object, the trace.stats dict like class must
        contain a obspy.core.util.AttribDict with 'latitude', 'longitude' (in
        degrees) and 'elevation' (in km), or 'x', 'y', 'elevation' (in km)
        items/attributes. See param coordsys
    :type sll_x: Float
    :param sll_x: slowness x min (lower)
    :type slm_x: Float
    :param slm_x: slowness x max
    :type sll_y: Float
    :param sll_y: slowness y min (lower)
    :type slm_y: Float
    :param slm_y: slowness y max
    :type sl_s: Float
    :param sl_s: slowness step
    :type stime: UTCDateTime
    :param stime: Starttime of interest
    :type etime: UTCDateTime
    :param etime: Endtime of interest
    :type win_len: Float
    :param window length for sliding window analysis, default is -1 which means
        the whole trace;
    :type win_frac: Float
    :param fraction of win_len which is used to 'hop' forward in time
    :param coordsys: valid values: 'lonlat' and 'xy', choose which stream
        attributes to use for coordinates
    :type timestamp: string
    :param timestamp: valid values: 'julsec' and 'mlabday'; 'julsec' returns
        the timestamp in secons since 1970-01-01T00:00:00, 'mlabday'
        returns the timestamp in days (decimals represent hours, minutes
        and seconds) since '0001-01-01T00:00:00' as needed for matplotlib
        date plotting (see e.g. matplotlibs num2date)
    :type method: string
    :param method: the method to use "DLS" delay and sum; "PWS" phase weigted
        stack; "SWP" slowness weightend power spectrum
    :type nthroot: Float
    :param nthroot: nth-root processing; nth gives the root (1,2,3,4), default
        1 (no nth-root)
    :type store: function
    :param store: A custom function which gets called on each iteration. It is
        called with the relative power map and the time offset as first and
        second arguments and the iteration number as third argument. Useful for
        storing or plotting the map for each iteration. For this purpose the
        dump function of this module can be used.
    :type correct_3dplane: Boolean
    :param correct_3dplane: if Yes than a best (LSQ) plane will be fitted into
        the array geometry.
        Mainly used with small apature arrays at steep flanks
    :type static_3D: Boolean
    :param static_3D: if yes the station height of am array station is taken
        into account accoring the formula:
            tj = -xj*sxj - yj*syj + zj*cos(inc)/vel_cor
        the inc angle is slowness dependend and thus must
        be estimated for each grid-point:
            inc = asin(v_cor*slow)
    :type vel_cor: Float
    :param vel_cor: Velocity for the upper layer (static correction) in km/s
    :return: numpy.ndarray of timestamp, relative relpow, absolute relpow,
        backazimut, slowness, maximum beam (for DLS)
    """
    res = []
    eotr = True

    # check that sampling rates do not vary
    fs = stream[0].stats.sampling_rate
    nstat = len(stream)
    if len(stream) != len(stream.select(sampling_rate=fs)):
        msg = 'in sonic sampling rates of traces in stream are not equal'
        raise ValueError(msg)

    # loop with a sliding window over the dat trace array and apply bbfk

    grdpts_x = int(((slm_x - sll_x) / sl_s + 0.5) + 1)
    grdpts_y = int(((slm_y - sll_y) / sl_s + 0.5) + 1)

    abspow_map = np.empty((grdpts_x, grdpts_y), dtype='f8')
    geometry = get_geometry(stream, coordsys=coordsys,
                            correct_3dplane=correct_3dplane, verbose=verbose)
    # geometry = get_geometry(stream, coordsys=coordsys, verbose=verbose)

    if verbose:
        print("geometry:")
        print(geometry)
        print("stream contains following traces:")
        print(stream)
        print("stime = " + str(stime) + ", etime = " + str(etime))

    time_shift_table = get_timeshift(geometry, sll_x, sll_y, sl_s, grdpts_x,
                                     grdpts_y, vel_cor=vel_cor,
                                     static_3D=static_3D)

    mini = np.min(time_shift_table[:, :, :])
    maxi = np.max(time_shift_table[:, :, :])
    spoint, _epoint = get_stream_offsets(stream, (stime-mini), (etime-maxi))
    minend = np.min(_epoint)
    maxstart = np.max(spoint)

    # recalculate the maximum possible trace length
    #    ndat = int(((etime-maxi) - (stime-mini))*fs)
    if(win_len < 0):
            nsamp = int(((etime-maxi) - (stime-mini))*fs)
    else:
        # nsamp = int((win_len-np.abs(maxi)-np.abs(mini)) * fs)
        nsamp = int(win_len * fs)

    if nsamp <= 0:
        print('Data window too small for slowness grid')
        print('Must exit')
        quit()

    nstep = int(nsamp * win_frac)

    stream.detrend()
    newstart = stime
    slow = 0.
    offset = 0
    count = 0
    while eotr:
        max_beam = 0.
        if method == 'DLS':
            for x in xrange(grdpts_x):
                for y in xrange(grdpts_y):
                    singlet = 0.
                    beam = np.zeros(nsamp, dtype='f8')
                    for i in xrange(nstat):
                        s = spoint[i]+int(time_shift_table[i, x, y] * fs + 0.5)
                        try:
                            shifted = stream[i].data[s + offset:
                                                     s + nsamp + offset]
                            if len(shifted) < nsamp:
                                shifted = np.pad(
                                    shifted, (0, nsamp-len(shifted)),
                                    'constant', constant_values=(0, 1))
                            singlet += 1./nstat*np.sum(shifted*shifted)
                            beam += 1. / nstat * np.power(
                                np.abs(shifted), 1. / nthroot) * \
                                shifted / np.abs(shifted)
                        except IndexError:
                            break
                    beam = np.power(np.abs(beam), nthroot) * \
                        beam / np.abs(beam)
                    bs = np.sum(beam*beam)
                    abspow_map[x, y] = bs / singlet
                    if abspow_map[x, y] > max_beam:
                        max_beam = abspow_map[x, y]
                        beam_max = beam
        if method == 'PWS':
            for x in xrange(grdpts_x):
                for y in xrange(grdpts_y):
                    singlet = 0.
                    beam = np.zeros(nsamp, dtype='f8')
                    stack = np.zeros(nsamp, dtype='c8')
                    for i in xrange(nstat):
                        s = spoint[i] + int(time_shift_table[i, x, y] * fs +
                                            0.5)
                        try:
                            shifted = sp.signal.hilbert(stream[i].data[
                                s + offset: s + nsamp + offset])
                            if len(shifted) < nsamp:
                                shifted = np.pad(
                                    shifted, (0, nsamp-len(shifted)),
                                    'constant', constant_values=(0, 1))
                        except IndexError:
                            break
                        phase = np.arctan2(shifted.imag, shifted.real)
                        stack.real += np.cos(phase)
                        stack.imag += np.sin(phase)
                    coh = 1. / nstat * np.abs(stack)
                    for i in xrange(nstat):
                        s = spoint[i]+int(time_shift_table[i, x, y] * fs + 0.5)
                        shifted = stream[i].data[s+offset: s + nsamp + offset]
                        singlet += 1. / nstat * np.sum(shifted * shifted)
                        beam += 1. / nstat * shifted * np.power(coh, nthroot)
                    bs = np.sum(beam*beam)
                    abspow_map[x, y] = bs / singlet
                    if abspow_map[x, y] > max_beam:
                        max_beam = abspow_map[x, y]
                        beam_max = beam
        if method == 'SWP':
            # generate plan for rfftr
            nfft = nextpow2(nsamp)
            deltaf = fs / float(nfft)
            nlow = int(frqlow / float(deltaf) + 0.5)
            nhigh = int(frqhigh / float(deltaf) + 0.5)
            nlow = max(1, nlow)  # avoid using the offset
            nhigh = min(nfft / 2 - 1, nhigh)  # avoid using nyquist
            nf = nhigh - nlow + 1  # include upper and lower frequency

            beam = np.zeros((grdpts_x, grdpts_y, nf), dtype='f16')
            steer = np.empty((nf, grdpts_x, grdpts_y, nstat), dtype='c16')
            spec = np.zeros((nstat, nf), dtype='c16')
            time_shift_table *= -1.
            clibsignal.calcSteer(nstat, grdpts_x, grdpts_y, nf, nlow,
                                 deltaf, time_shift_table, steer)
            try:
                for i in xrange(nstat):
                    dat = stream[i].data[spoint[i] + offset:
                                         spoint[i] + offset + nsamp]
                    dat = (dat - dat.mean()) * tap
                    spec[i, :] = np.fft.rfft(dat, nfft)[nlow: nlow + nf]
            except IndexError:
                break

            for i in xrange(grdpts_x):
                for j in xrange(grdpts_y):
                    for k in xrange(nf):
                        for l in xrange(nstat):
                            steer[k, i, j, l] *= spec[l, k]

            beam = np.absolute(np.sum(steer, axis=3))
            less = np.max(beam, axis=1)
            max_buffer = np.max(less, axis=1)

            for i in xrange(grdpts_x):
                for j in xrange(grdpts_y):
                    abspow_map[i, j] = np.sum(beam[:, i, j] / max_buffer[:],
                                              axis=0) / float(nf)

            beam_max = stream[0].data[spoint[0] + offset:
                                      spoint[0] + nsamp + offset]

        ix, iy = np.unravel_index(abspow_map.argmax(), abspow_map.shape)
        abspow = abspow_map[ix, iy]
        if store is not None:
            store(abspow_map, beam_max, count)
        count += 1
        print(count)
        # here we compute baz, slow
        slow_x = sll_x + ix * sl_s
        slow_y = sll_y + iy * sl_s

        slow = np.sqrt(slow_x ** 2 + slow_y ** 2)
        if slow < 1e-8:
            slow = 1e-8
        azimut = 180 * math.atan2(slow_x, slow_y) / math.pi
        baz = azimut % -360 + 180
        res.append(np.array([newstart.timestamp, abspow, baz, slow_x, slow_y,
                             slow]))
        if verbose:
            print(newstart, (newstart + (nsamp / fs)), res[-1][1:])
        if (newstart + (nsamp + nstep) / fs) > etime:
            eotr = False
        offset += nstep

        newstart += nstep / fs
    res = np.array(res)
    if timestamp == 'julsec':
        pass
    elif timestamp == 'mlabday':
        # 719162 == hours between 1970 and 0001
        res[:, 0] = res[:, 0] / (24. * 3600) + 719162
    else:
        msg = "Option timestamp must be one of 'julsec', or 'mlabday'"
        raise ValueError(msg)
    return np.array(res)


#    return(baz,slow,slow_x,slow_y,abspow_map,beam_max)


def vespagram_baz(stream, time_shift_table, starttime, endtime,
                  method="DLS", nthroot=1):
    """
    Estimating the azimuth or slowness vespagram

    :param stream: Stream object, the trace.stats dict like class must
        contain a obspy.core.util.AttribDict with 'latitude', 'longitude' (in
        degrees) and 'elevation' (in km), or 'x', 'y', 'elevation' (in km)
        items/attributes. See param coordsys
    :type starttime: UTCDateTime
    :param starttime: Starttime of interest
    :type endtime: UTCDateTime
    :param endtime: Endtime of interest
    :return: numpy.ndarray of beams with different slownesses
    """
    fs = stream[0].stats.sampling_rate

    mini = min(min(i.values()) for i in time_shift_table.values())
    maxi = max(max(i.values()) for i in time_shift_table.values())
    spoint, _ = get_stream_offsets(stream, (starttime - mini),
                                   (endtime - maxi))

    # Recalculate the maximum possible trace length
    ndat = int(((endtime - maxi) - (starttime - mini)) * fs)
    beams = np.zeros((len(time_shift_table), ndat), dtype='f8')

    max_beam = 0.0
    slow = 0.0

    slownesses = sorted(time_shift_table.keys())
    sll = slownesses[0]
    sls = slownesses[1] - sll

    for _i, slowness in enumerate(time_shift_table.keys()):
        singlet = 0.0
        if method == 'DLS':
            for _j, tr in stream:
                station = "%s.%s" % (tr.stats.network, tr.stats.station)
                s = spoint[_j] + int(time_shift_table[slowness][station] *
                                     fs + 0.5)
                shifted = tr.data[s: s + ndat]
                singlet += 1. / len(stream) * np.sum(shifted * shifted)
                beams[_i] += 1. / len(stream) * np.power(
                    np.abs(shifted), 1. / nthroot) * shifted / np.abs(shifted)

            beams[_i] = np.power(np.abs(beams[_i]), nthroot) * beams[_i] / \
                np.abs(beams[_i])

            bs = np.sum(beams[_i] * beams[_i])
            bs /= singlet

            if bs > max_beam:
                max_beam = bs
                beam_max = _i
                slow = np.abs(sll + slowness * sls)

        elif method == 'PWS':
            stack = np.zeros(ndat, dtype='c8')
            for i in xrange(nstat):
                s = spoint[i] + int(time_shift_table[i, x] * fs + 0.5)
                try:
                    shifted = sp.signal.hilbert(stream[i].data[s:s + ndat])
                except IndexError:
                    break
                phase = np.arctan2(shifted.imag, shifted.real)
                stack.real += np.cos(phase)
                stack.imag += np.sin(phase)
            coh = 1. / nstat * np.abs(stack)
            for i in xrange(nstat):
                s = spoint[i]+int(time_shift_table[i, x] * fs + 0.5)
                shifted = stream[i].data[s: s + ndat]
                singlet += 1. / nstat * np.sum(shifted * shifted)
                beams[x] += 1. / nstat * shifted * np.power(coh, nthroot)
            bs = np.sum(beams[x]*beams[x])
            bs = bs / singlet
            if bs > max_beam:
                max_beam = bs
                beam_max = x
                slow = np.abs(sll + x * sls)
                if (slow) < 1e-8:
                    slow = 1e-8
        else:
            msg = "Method '%s' unknown." % method
            raise ValueError(msg)

    return(slow, beams, beam_max, max_beam)


def shifttrace_freq(stream, t_shift):
    if isinstance(stream, Stream):
        for i, tr in enumerate(stream):
            ndat = tr.stats.npts
            samp = tr.stats.sampling_rate
            nfft = nextpow2(ndat)
            nfft *= 2
            tr1 = np.fft.rfft(tr.data, nfft)
            for k in xrange(0, nfft / 2):
                tr1[k] *= np.complex(
                    np.cos((t_shift[i] * samp) * (k / float(nfft))
                           * 2. * np.pi),
                    -np.sin((t_shift[i] * samp) *
                            (k / float(nfft)) * 2. * np.pi))

            tr1 = np.fft.irfft(tr1, nfft)
            tr.data = tr1[0:ndat]


if __name__ == '__main__':
    import doctest
    doctest.testmod(exclude_empty=True)
