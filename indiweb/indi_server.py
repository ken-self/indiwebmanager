#!/usr/bin/python
import logging
import os
from subprocess import call, check_output
import threading
import queue

# Local imports
from .AsyncSystemCommand import AsyncSystemCommand

INDI_PORT = 7624
INDI_FIFO = '/tmp/indiFIFO'
try:
    INDI_CONFIG_DIR = os.path.join(os.environ['HOME'], '.indi')
except KeyError:
    INDI_CONFIG_DIR = '/tmp/indi'

class IndiServer(object):
    def __init__(self, fifo=INDI_FIFO, conf_dir=INDI_CONFIG_DIR):
        self.__fifo = fifo
        self.__sock_path = f"{self.__fifo}_sock"
        self.__conf_dir = conf_dir
        self.__async_cmd = None
        self.__command_thread = None
        self.__running_drivers = {}
        self.__driver_starter_thread = None

    def __driver_starter_worker(self, driver_queue):
        """Worker thread to start drivers sequentially from a queue."""
        while not driver_queue.empty():
            try:
                driver = driver_queue.get_nowait()
                logging.info(f"Worker thread starting driver: {driver.label}")
                self.start_driver(driver)
                driver_queue.task_done()
                logging.info(f"Worker thread finished starting driver: {driver.label}")
            except queue.Empty:
                # Queue became empty between check and get, just exit
                break
            except Exception as e:
                # Log any other exceptions during driver start
                logging.error(f"Error starting driver {driver.label if 'driver' in locals() else 'unknown'} in worker thread: {e}")
                # Optionally, mark task as done even on error to prevent blocking join() if used later
                # driver_queue.task_done() 
                # Decide if one driver error should stop others, currently it continues
                continue
        logging.info("Driver starter worker thread finished.")


    def __clear_fifo(self):
        logging.info("Deleting fifo %s" % self.__fifo)
        call(['rm', '-f', self.__fifo])
        call(['mkfifo', self.__fifo])

    def __run(self, port):
        # Store the port for later use
        self.__port = port
        cmd = 'indiserver -p %d -m 1000 -v -f %s -u %s > /tmp/indiserver.log 2>&1' % \
            (port, self.__fifo, self.__sock_path)
        logging.info(cmd)
        self.__async_cmd = AsyncSystemCommand(cmd)
        # Run the command asynchronously
        self.__command_thread = threading.Thread(target=self.__async_cmd.run)
        self.__command_thread.start()

    def start_driver(self, driver):
        # escape quotes if they exist
        logging.info("Starting driver: " + driver.label)
        cmd = 'start '
        try:
            cmd += driver.binary
        except AttributeError:
            logging.error("Driver is missing binary field. Is it installed? Please reinstall the driver!")
            return

        if driver.skeleton:
            cmd += ' -s "%s"' % driver.skeleton

        # Only add the label if it's not a remote driver (doesn't contain @)
        # If MDPD is enabled, don't add the label as the driver will create multiple devices
        if "@" not in driver.binary and not driver.mdpd:
                cmd += ' -n "%s"' % driver.label

        rule = driver.rule
        # Check if we have script rule for pre driver startup
        if rule:
            pre_delay = rule.get("PreDelay", 0)
            if pre_delay > 0:
                logging.info("Delaying driver startup by Pre Delay %d second(s)..." % pre_delay)
                import time
                time.sleep(pre_delay)
            pre_script = rule.get("PreScript")
            if pre_script:
                logging.info("Running Pre Script %s" % pre_script)
                try:
                    output = check_output(pre_script).decode('utf_8')
                except Exception as error:
                    logging.warning("Pre Script failed to execute: %s. Aborting..." % error)
                    return
                logging.info(output)

        cmd = cmd.replace('"', '\\"')
        full_cmd = 'echo "%s" > %s' % (cmd, self.__fifo)
        logging.info(full_cmd)
        call(full_cmd, shell=True)

        # Check if we have script rule for post driver startup
        if rule:
            post_delay = rule.get("PostDelay", 0)
            if post_delay > 0:
                logging.info("Delaying post driver startup by Post Delay %d second(s)..." % post_delay)
                import time
                time.sleep(post_delay)
            post_script = rule.get("PostScript")
            if post_script:
                logging.info("Running Post Script %s" % post_script)
                try:
                    output = check_output(post_script).decode('utf_8')
                except Exception as error:
                    logging.warning("Post Script failed to execute: %s. Aborting..." % error)
                    return
                logging.info(output)

        self.__running_drivers[driver.label] = driver

    def stop_driver(self, driver, device_label=None):
        driver_label = driver.label
        if device_label:
            driver_label = device_label

        rule = driver.rule
        # Check if we have script rule for pre driver shutdown
        if rule:
            stopping_script = rule.get("StoppingScript")
            if stopping_script:
                logging.info("Running Pre Shutdown Script %s" % stopping_script)
                try:
                    output = check_output(stopping_script).decode('utf_8')
                except Exception as error:
                    logging.warning("Pre Shutdown Script failed to execute: %s. Aborting..." % error)
                    return
                logging.info(output)
            stopping_delay = rule.get("StoppingDelay", 0)
            if stopping_delay > 0:
                logging.info("Delaying driver shutdown by Stopping Delay %d second(s)..." % stopping_delay)
                import time
                time.sleep(stopping_delay)

        # escape quotes if they exist
        logging.info("Stopping driver: " + driver_label)
        cmd = 'stop '
        try:
            cmd += driver.binary
        except AttributeError:
            logging.error("Driver is missing binary field. Is it installed? Please reinstall the driver!")
            return

        # If MDPD is enabled, don't add the label as the driver will create multiple devices
        if "@" not in driver.binary and not driver.mdpd:
                cmd += ' -n "%s"' % driver_label

        cmd = cmd.replace('"', '\\"')
        full_cmd = 'echo "%s" > %s' % (cmd, self.__fifo)
        logging.info(full_cmd)
        call(full_cmd, shell=True)

        # Check if we have script rule for post driver shutdown
        if rule:
            stopped_delay = rule.get("StoppedDelay", 0)
            if stopped_delay > 0:
                logging.info("Delaying post driver shutdown by Post Delay %d second(s)..." % stopped_delay)
                import time
                time.sleep(stopped_delay)
            stopped_script = rule.get("StoppedScript")
            if stopped_script:
                logging.info("Running Post Shutdown Script %s" % stopped_script)
                try:
                    output = check_output(stopped_script).decode('utf_8')
                except Exception as error:
                    logging.warning("Post Shutdown Script failed to execute: %s. Aborting..." % error)
                    return
                logging.info(output)

        del self.__running_drivers[driver.label]

    def start(self, port=INDI_PORT, drivers=[]):
        if self.is_running(port):
            self.stop(port)

        self.__clear_fifo()
        self.__run(port)
        # Reset running drivers list immediately
        self.__running_drivers = {}

        if drivers:
            driver_queue = queue.Queue()
            for driver in drivers:
                driver_queue.put(driver)

            # Start the driver starter worker thread
            self.__driver_starter_thread = threading.Thread(
                target=self.__driver_starter_worker,
                args=(driver_queue,),
                daemon=True  # Set as daemon so it doesn't block program exit
            )
            logging.info("Starting background thread for driver initialization.")
            self.__driver_starter_thread.start()
        else:
            logging.info("No drivers specified to start.")


    def stop(self, port=None):
        # If port is not specified, use the port from the last start command
        if port is None:
            port = getattr(self, '_IndiServer__port', INDI_PORT)
            
        # Try to find and kill the indiserver process running on the specified port
        try:
            import psutil
            for proc in psutil.process_iter(['name', 'cmdline']):
                if proc.info['name'] == 'indiserver':
                    # Check if this indiserver is running on our port
                    cmdline = proc.cmdline()
                    for i, arg in enumerate(cmdline):
                        if arg == '-p' and i + 1 < len(cmdline):
                            if int(cmdline[i + 1]) == port:
                                logging.info(f"Killing indiserver process {proc.pid} running on port {port}")
                                proc.kill()
                                proc.wait()
                                logging.info(f"indiserver on port {port} terminated successfully")
                                break
        except (ImportError, psutil.Error, ValueError, IndexError) as e:
            # If psutil is not available or there's an error, fall back to the original method
            logging.warning(f"Error killing indiserver process: {str(e)}")
            cmd = ['pkill', '-9', 'indiserver']
            logging.info(' '.join(cmd))
            ret = call(cmd)
            if ret == 0:
                logging.info('indiserver terminated successfully')
            else:
                logging.warn('terminating indiserver failed code ' + str(ret))
        
        # Also terminate our async command if it's running
        try:
            if self.__async_cmd:
                self.__async_cmd.terminate()
                self.__command_thread.join()
        except Exception as e:
            logging.warn('indi_server: termination of async command failed with error ' + str(e))

    def is_running(self, port=None):
        # If port is not specified, use the port from the last start command
        if port is None:
            port = getattr(self, '_IndiServer__port', INDI_PORT)
        
        # First check if our async command is running
        if self.__async_cmd and self.__async_cmd.is_running():
            return True
        
        # If not, check if there's an indiserver process running on the specified port
        try:
            import psutil
            for proc in psutil.process_iter(['name', 'cmdline']):
                if proc.info['name'] == 'indiserver':
                    # Check if this indiserver is running on our port
                    cmdline = proc.cmdline()
                    for i, arg in enumerate(cmdline):
                        if arg == '-p' and i + 1 < len(cmdline):
                            if int(cmdline[i + 1]) == port:
                                return True
        except (ImportError, psutil.Error, ValueError, IndexError) as e:
            # If psutil is not available or there's an error, log it and fall back to the original method
            logging.warning(f"Error checking for indiserver process: {str(e)}")
            
        return False

    def set_prop(self, dev, prop, element, value):
        cmd = ['indi_setprop', '%s.%s.%s=%s' % (dev, prop, element, value)]
        call(cmd)

    def get_prop(self, dev, prop, element):
        cmd = ['indi_getprop', '%s.%s.%s' % (dev, prop, element)]
        output = check_output(cmd)
        return output.split('=')[1].strip()

    def get_state(self, dev, prop):
        return self.get_prop(dev, prop, '_STATE')

    def auto_connect(self):
        cmd = ['indi_getprop', '*.CONNECTION.CONNECT']
        output = ""
        try:
            output = check_output(cmd).decode('utf_8')
        except Exception as e:
            logging.error(e)

        output = output.replace("Off", "On")

        for dev in output.splitlines():
            command = ['indi_setprop', dev]
            logging.info(command)
            call(command)

    def get_running_drivers(self):
        drivers = self.__running_drivers
        return drivers
