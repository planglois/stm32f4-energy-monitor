import usb
import usb.core
import usb.util

from struct import *
from copy import copy
from collections import namedtuple

from logging import warning, error

# import multiprocessing

Measurement = namedtuple('Measurement', 'energy time peak_power peak_voltage peak_current n_samples avg_voltage avg_current')

class EnergyMonitor(object):
    MeasurementData = namedtuple('MeasurementData', 'energy_accum elapsed_time peak_power peak_voltage peak_current n_samples avg_current avg_voltage')
    MeasurementData_packing = "=QQLLLLQQ"

    InstantaneousData = namedtuple('InstantaneousData', 'voltage current current_time')
    InstantaneousData_packing = "=LLL"

    ADC1 = 0
    ADC2 = 1
    ADC3 = 2

    port_mappings = {1: ["PA2", "PC2"], 2: ["PA3", "PA1"], 3: ["PB1", "PC5"], 4:["PB0", "PC4"]}

    def __init__(self, serial="EE00"):
        # Find the usb device that corresponds to the serial number
        #devs = usb.core.find(idVendor=0xf539, idProduct=0xf539,
        #    find_all = True, custom_match=lambda d: usb.util.get_string(d, 256, d.iSerialNumber) == serial)
        devs = usb.core.find(idVendor=0xf539, idProduct=0xf539,
            find_all = True)

        if len(devs) > 1:
            warning("More than one device available with serial " + serial)
        if len(devs) == 0:
            raise RuntimeError("Cannot find energy monitor with serial " + serial)

        self.serial = serial
        self.dev = devs[0]

        # Set up default parameters for each measurement point
        defaultparams = {'resistor':1, 'gain':50, 'vref':3}
        self.measurement_params = {i : copy(defaultparams) for i in [1,2,3]}

        # Measurement point 4 is the 'self' measurement point
        self.measurement_params[4] = {'resistor':0.5, 'gain':50, 'vref':3}

        # Equal to tperiod in the firmware
        self.samplePeriod = 500

        self.adcMpoint = [None, None, None]

    # Connect to the device
    def connect(self):
        self.dev.set_configuration()

    # Toggle the LEDs on the device
    def toggleLEDs(self):
        self.dev.ctrl_transfer(0x41, 0, 0, 0, None)

    # Start measuring on m_point
    def start(self, m_point=1):
        self.clearNumberOfRuns()
        self.dev.ctrl_transfer(0x41, 1, int(m_point), 0, None)

    # Stop measuring on m_point
    def stop(self, m_point=1):
        self.dev.ctrl_transfer(0x41, 2, int(m_point), 0, None)

    # Return whether the measurement point is currently taking
    # measurements or not
    def isRunning(self, m_point=1):
        b = self.dev.ctrl_transfer(0xc1, 8, int(m_point), 0, 4)

        running = unpack("=L", b)
        return bool(running[0])

    # This counts the number of end measurement signals caught
    # by the energy monitor.
    def getNumberOfRuns(self, m_point=1):
        b = self.dev.ctrl_transfer(0xc1, 9, int(m_point), 0, 4)

        runs = unpack("=L", b)
        return bool(runs[0])

    # Reset the number of runs counts to 0
    def clearNumberOfRuns(self, m_point=1):
        self.dev.ctrl_transfer(0x41, 10, int(m_point), 0, None)

    # Have we completed a measurement?
    def measurementCompleted(self, m_point = 1):
        runs = self.getNumberOfRuns()
        if runs > 1:
            warning("More than one measurement has completed (expected one)")
        if not self.isRunning(m_point) and runs > 0:
            self.clearNumberOfRuns()
            return True
        return False

    # Set a particular port as a pin trigger for a measurement point
    #   e.g PA0
    def setTrigger(self, port, m_point=1):

        # TODO check port is of the form PA0
        self.dev.ctrl_transfer(0x41, 4, ord(port[1]) | (m_point<<8), int(port[2]), None)

    # Enable a particular measurement point. There are
    # only 3 ADCs in the device, so only 3 measurement points
    # can be used simultaneously
    def enableMeasurementPoint(self, m_point, adc=None):
        if m_point in self.adcMpoint:
            warning("Tried to enable already enabled measurement point "+str(m_point))
            return
        if self.adcMpoint.count(None) == 0:
            raise RuntimeError("Cannot enable measurement point {}. Maximum of enabled measurement points reached".format(m_point))
        if adc is not None and self.adcMpoint[adc] is not None:
            raise RuntimeError("Cannot enable map measurement point {0} to ADC{1}. ADC{1} already has measurement point {2}".format(m_point, adc, self.adcMpoint[adc]))

        if adc is None:
            # If we want mpoint 1 or 2 and ADC3 is free, prioritise it
            #  becuase ADC3 doesnt work with mpoint 3 or self
            if m_point in [1, 2] and self.adcMpoint[2] is None:
                adc = 2
            else:
                adc = self.adcMpoint.index(None)
        if m_point in [3,4] and adc == 2:
            raise RuntimeError("Measurement point {} cannot be used with ADC3 (the only free ADC)".format(m_point))
        self.adcMpoint[adc] = m_point
        self.dev.ctrl_transfer(0x41, 7, int(m_point), adc, None)

    # Disable a particular measurement point, so that the
    # ADC could potentially be used with a different one
    def disableMeasurementPoint(self, m_point):
        if m_point not in self.adcMpoint:
            warning("Tried to disable already disabled measurement point "+str(m_point))
            return
        adc = self.adcMpoint.index(m_point)
        self.adcMpoint[adc] = None
        # TODO: perhaps a control transfer to actually disable the mpoint

    def convertData(self, md, resistor, gain, vref, samplePeriod):
        en = float(vref)**2 / gain / resistor / 4096**2 * 2 * samplePeriod * 2 / 168000000. * md.energy_accum * 2
        el = md.elapsed_time * 2. / 168000000 * 2
        pp = float(vref)**2 / gain / resistor / 4096**2 * md.peak_power * 2
        pv = float(vref) / 4096. * md.peak_voltage * 2
        pi = float(vref) / gain / resistor / 4096. * md.peak_current
        av = float(vref) / 4096. * md.avg_voltage / md.n_samples * 2
        ai = float(vref) / gain / resistor / 4096. * md.avg_current / md.n_samples

        m = Measurement(en, el, pp, pv, pi, md.n_samples, av, ai)

        return m

    def getMeasurement(self, m_point=1):
        b = self.dev.ctrl_transfer(0xc1, 6, int(m_point), 0, 48)
        u = EnergyMonitor.MeasurementData._make(unpack(EnergyMonitor.MeasurementData_packing, b))

        return self.convertData(u, samplePeriod=self.samplePeriod, **self.measurement_params[m_point])

    # get an instantaneous measurement of voltage and current (debugging)
    def getInstantaneous(self, m_point=1):
        b = self.dev.ctrl_transfer(0xc1, 11, int(m_point), 0, 12)
        args = list(unpack(EnergyMonitor.InstantaneousData_packing, b))
        args.append(m_point)
        return args

    # Convert and display instantaneous measurement
    def debugInstantaneous(self, v):
        resistor = self.measurement_params[v[3]]['resistor']
        gain = self.measurement_params[v[3]]['gain']
        vref = self.measurement_params[v[3]]['vref']

        print "Timestamp:", v[2] * 2. / 168000000 * 2
        print "Current:  Raw={:4d}  Voltage@{}={:1.5f}  Current={:1.6f}".format(v[1],
            EnergyMonitor.port_mappings[v[3]][1],
            v[1]/4096.*vref,
            float(vref) / gain / resistor / 4096. * v[1])
        print "Voltage:  Raw={:4d}  Voltage@{}={:1.5f}  Voltage={:1.6f}".format(v[0],
            EnergyMonitor.port_mappings[v[3]][0],
            v[0]/4096.*vref,
            float(vref) / 4096. * v[0] * 2)
        print ""

    def disconnect(self):
        pass



if __name__ == "__main__":
    from time import sleep

    em = EnergyMonitor("EE00")
    em.connect()

    em.toggleLEDs()

    em.enableMeasurementPoint(1)
    em.enableMeasurementPoint(2)

    em.setTrigger("PA0", 1)
    em.setTrigger("PA0", 2)

    print "*** Press the blue button to make a measurement"

    while True:
        while not em.measurementCompleted(): sleep(0.1)
        print em.getMeasurement(1)
        print em.getMeasurement(2)
