import os
import subprocess
import logging
import tempfile
import socket
import shutil
import time
import glob
import signal
import sys
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
FNULL = open(os.devnull, 'w')

SIG_FREEZETIME = signal.SIGURG
SIG_STEPTIME = signal.SIGUSR2

class FilePatch(object):
    def __init__(self, path, patcher):
        self.patcher = patcher
        self.path = path

    def __enter__(self):
        if not os.path.exists(self.path + ".pebble-recorder-bak"):
            shutil.copy(self.path, self.path + ".pebble-recorder-bak")
        else:
            if not os.path.exists(self.path):
                shutil.copy(self.path + ".pebble-recorder-bak", self.path)
        module_f = open(self.path, "r+")
        contents = module_f.read()
        contents = self.patcher(contents)
        module_f.seek(0)
        module_f.write(contents)
        module_f.truncate()
        module_f.close()
        return self

    def __exit__(self, type, value, traceback):
        if not os.path.exists(self.path + ".pebble-recorder-bak"):
            raise RuntimeError("Can't unpatch without .pebble-recorder-bak")
        if os.path.exists(self.path):
            os.remove(self.path)
        if os.path.exists(self.path + ".pyc"):
            os.remove(self.path + ".pyc")
        os.rename(self.path + ".pebble-recorder-bak", self.path)


class PebbleRecorder:
    _sdk_dir = None
    platform = None
    _qmp_socket_path = os.path.join(tempfile.gettempdir(), "pebble-qemu-pr-qmp")
    _timestep_driver_params_path = os.path.join(tempfile.gettempdir(), "pebble-qemu-pr-driver-params")

    @property
    def sdk_dir(self):
        if not self._sdk_dir:
            try:
                # This is terrible
                self._sdk_dir = os.path.join(subprocess.check_output("which pebble", shell=True).strip()[:-6], "..")
            except subprocess.CalledProcessError:
                raise RuntimeError("Could not locate pebble tool - sure it's installed?")
        return self._sdk_dir

    @property
    def sdk_arm_bin_dir(self):
        return os.path.join(self.sdk_dir, "arm-cs-tools", "arm-none-eabi", "bin")

    @property
    def sdk_pebble_tool(self):
        return os.path.join(self.sdk_dir, "bin", "pebble")

    @property
    def sdk_pebble_emulator_module(self):
        return os.path.join(self.sdk_dir, "tools", "pebble", "PebbleEmulator.py")

    @property
    def sdk_pebble_emulator_module(self):
        return os.path.join(self.sdk_dir, "tools", "pebble", "PebbleEmulator.py")

    @property
    def sdk_waf_dir(self):
        return os.path.join(self.sdk_dir, "Pebble", glob.glob(os.path.join(self.sdk_dir, "Pebble", ".waf*"))[0])

    @property
    def sdk_waf_metadata_inject_module(self):
        return os.path.join(self.sdk_waf_dir, "waflib", "extras", "inject_metadata.py")

    @property
    def pr_dir(self):
        return os.path.dirname(os.path.realpath(__file__))

    @property
    def qemu_pid(self):
        return int(open(os.path.join(tempfile.gettempdir(), "pebble-qemu.pid"), "r").read())

    def check_project_dir(self):
        if not os.path.exists("appinfo.json"):
            raise RuntimeError("Please run pebble-recorder from your Pebble project's root")

    def compile_with_forced_backlight(self):
        # There's no option to disable the silly backlight simulator
        # So, we recompile the app to call light_enable(true) at boot
        # Don't bother if we've already compiled it as such
        if os.path.exists("build"):
            if ".pebble_recorder_light_override" in open(os.path.join("build", "pebble-app.map")).read():
                logger.info("App already built with backlight override")
                return

        logger.info("Recompiling app with backlight override...")

        # We overwrite app_event_loop with our own copy that calls light_enable before yielding to the original
        # Originally I was changing the app entrypoint, but that required patching inject_metadata.py in the SDK to pull the new entrypoint
        light_override_path = "src/.pebble_recorder_light_override.c"
        with FilePatch("wscript", lambda x: x.replace("ctx.pbl_program(", "ctx.pbl_program(linkflags=['-Wl,-wrap,app_event_loop'],")):
            with open(light_override_path, "w") as f:
                f.write("#include <pebble.h>\nextern void __real_app_event_loop(void);void __wrap_app_event_loop() {light_enable(1);__real_app_event_loop();}")
            try:
                subprocess.check_output([self.sdk_pebble_tool, "build"], stderr=FNULL)
            finally:
                os.remove(light_override_path)

    def _connect_qmp(self):
        self._qmp_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._qmp_sock.connect(self._qmp_socket_path)
        # Initialize the shell
        self._qmp_sock.sendall('{"execute": "qmp_capabilities"}')

    def boot_emulator(self):
        # We need to boot the emulator with QMP enabled, for screendumps
        # Additionally, we need to insert our dylib/so that controls gettimeofday
        if os.path.exists(self._qmp_socket_path) and False:
            try:
                self._connect_qmp()
            except:
                # Not openable = not running
                pass
            else:
                logger.info("Patched emulator already booted")
                return
        logger.info("Starting patched emulator")
        # subprocess.Popen([self.sdk_pebble_tool, "kill"]).wait()

        timestep_env = os.environ.copy()
        timestep_env["DYLD_FORCE_FLAT_NAMESPACE"] = "1"
        timestep_env["DYLD_INSERT_LIBRARIES"] = os.path.join(self.pr_dir, "timestep.dylib")
        timestep_env["LD_PRELOAD"] = os.path.join(self.pr_dir, "timestep.so")
        timestep_env["TIMESTEP"] = str(33333 * 1000) # ns
        # Really Bad Things happen if you outright freeze the RTC
        # So we keep the clock moving at a tiny fraction of realtime to keep it happy
        timestep_env["TIMESTEP_IDLE"] = str(100) # Also ns
        timestep_env["DRIVER_PARAMS"] = self._timestep_driver_params_path

        open(self._timestep_driver_params_path, "w").write("%d %d %d" % (33333 * 1000, 100, os.getpid()))

        # This is silly
        # We setpgrp() so the emulator doesn't bail when the user uses SIGINT to halt recording
        with FilePatch(self.sdk_pebble_emulator_module, lambda x: x.replace('cmdline = [qemu_bin]', "cmdline = [qemu_bin, \"-qmp\", \"unix:%s,server,nowait\"]\n        os.setpgrp()" % self._qmp_socket_path)):
            subprocess.check_output([self.sdk_pebble_tool, "install", "--emulator", self.platform], env=timestep_env)

        self._connect_qmp()

    def capture_loop(self):

        do_capture = True
        rtc_interlock = threading.Semaphore(0)
        def interlock_release(signo, frame):
            rtc_interlock.release()

        signal.signal(signal.SIGUSR1, interlock_release)

        if not os.path.exists(".pr-captures"):
            os.mkdir(".pr-captures")

        for filen in glob.glob(".pr-captures/*"):
            os.remove(os.path.join(".pr-captures", filen))

        qemu_pid = self.qemu_pid
        # Freeze time in the emulator
        raw_input("Press enter to start recording!")
        logger.info("Capturing RTC...")
        os.kill(qemu_pid, SIG_FREEZETIME)
        logger.info("Beginning capture (ctrl-C to finish recording):")
        frames = 0
        try:
            while True:
                # Step the emulator RTC
                os.kill(qemu_pid, SIG_STEPTIME)
                # Wait for RTC to catch up
                # We have to busywait, otherwise the signal will never arrive
                while not rtc_interlock.acquire(False):
                    time.sleep(0.01)
                self._qmp_sock.sendall("{\"execute\":\"screendump\",\"arguments\":{\"filename\":\".pr-captures/%d\"}}" % frames)
                # time.sleep(0.033 * 2)
                sys.stdout.write("\r%d frames NOT! captured" % frames)
                sys.stdout.flush()
                frames += 1
                # raw_input() q
        except KeyboardInterrupt:
            pass
        sys.stdout.write("\n")
        logger.info("Releasing RTC...")
        os.kill(qemu_pid, SIG_FREEZETIME)
    def run(self, platform):
        self.platform = platform
        self.check_project_dir()
        self.compile_with_forced_backlight()
        self.boot_emulator()
        self.capture_loop()


def run():
    PebbleRecorder().run("basalt")
