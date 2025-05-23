#!/usr/bin/python

import os
import logging
import xml.etree.ElementTree as ET

# Default INDI data directory
INDI_DATA_DIR = os.environ.get('INDI_DATA_DIR', "/usr/share/indi/")


class DeviceDriver:
    """Device driver container"""

    def __init__(self, name, label, version, binary, family, skel=None, mdpd=False, custom=False, rule=None):
        self.name = name
        self.label = label
        self.skeleton = skel
        self.version = version
        self.binary = binary
        self.family = family
        self.mdpd = mdpd
        self.custom = custom
        self.rule = rule
        self.role = ""


class DriverCollection:
    """A collection of drivers"""

    def __init__(self, path=INDI_DATA_DIR):
        self.path = path
        self.drivers = []
        self.files = []
        self.parse_drivers()

    def parse_drivers(self):
        for fname in os.listdir(self.path):
            # Skip Skeleton files
            if fname.endswith('.xml') and '_sk' not in fname:
                self.files.append(os.path.join(self.path, fname))

        for fname in self.files:
            try:
                tree = ET.parse(fname)
                root = tree.getroot()

                for group in root.findall('devGroup'):
                    family = group.attrib['group']

                    for device in group.findall('device'):
                        label = device.attrib['label']
                        skel = device.attrib.get('skel', None)
                        mdpd = device.attrib.get('mdpd', None) == 'true'
                        drv = device.find('driver')
                        name = drv.attrib['name']
                        binary = drv.text
                        version = device.findtext('version', '0.0')

                        skel_file = os.path.join(self.path, skel) if skel else None
                        driver = DeviceDriver(name, label, version,
                                              binary, family, skel_file, mdpd, False, None)
                        self.drivers.append(driver)

            except KeyError as e:
                logging.error("Error in file %s: attribute %s not found" % (fname, e))
            except ET.ParseError as e:
                logging.error("Error in file %s: %s" % (fname, e))

        # Sort all drivers by label
        self.drivers.sort(key=lambda x: x.label)

    def parse_custom_drivers(self, drivers):
        for custom in drivers:
            driver = DeviceDriver(custom['name'], custom['label'], custom['version'], custom['exec'],
                                  custom['family'], None, False, True, None)
            self.drivers.append(driver)

    def clear_custom_drivers(self):
        self.drivers = list(filter(lambda driver: driver.custom is not True, self.drivers))

    def by_label(self, label):
        # Try first an exact match
        for driver in self.drivers:
            if driver.label == label:
                return driver
        # Try second as partial match
        for driver in self.drivers:
            if label.startswith(driver.label):
                return driver

    def by_name(self, name):
        for driver in self.drivers:
            if (driver.name == name):
                return driver

        return None

    def by_binary(self, binary):
        for driver in self.drivers:
            if (driver.binary == binary):
                return driver

        return None

    def get_families(self):
        families = {}
        for drv in self.drivers:
            if drv.family in families:
                families[drv.family].append(drv.label)
            else:
                families[drv.family] = [drv.label]
        return families
        
    def apply_rules(self, rules):
        """Apply rules to drivers based on their labels"""
        if not rules:
            return
            
        for rule in rules:
            driver_label = rule.get('Driver')
            
            if driver_label:
                driver = self.by_label(driver_label)
                if driver:
                    driver.rule = rule
