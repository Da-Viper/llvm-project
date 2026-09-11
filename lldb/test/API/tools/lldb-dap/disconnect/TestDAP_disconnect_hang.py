"""
Regression tests for lldb-dap disconnect being bounded even when server-side
cleanup would otherwise block. See also DAP.cpp::Disconnect and
testcase.py::cleanup_session.
"""

import time

from lldbsuite.test.decorators import *
from lldbsuite.test.lldbtest import *
from lldbsuite.test.tools.lldb_dap import DAPTestCaseBase
from lldbsuite.test.tools.lldb_dap.types import *


@requireNotWasm("no launch support")
class TestDAP_disconnect_hang(DAPTestCaseBase):
    SHARED_BUILD_TESTCASE = False

    source = "main.cpp"

    @skipIfWindows
    def test_disconnect_bounded_when_terminate_commands_hang(self):
        """
        A user terminateCommand that never returns must not block the client's
        disconnect response. Without the bound in DAP::SendTerminatedEvent this
        blocks for the full 50s DEFAULT_TIMEOUT and the request times out.
        """
        program = self.getBuildArtifact("a.out")
        session = self.build_and_create_session(disconnect_automatically=False)
        launch_args = LaunchArgs(
            program,
            stopOnEntry=True,
            terminateCommands=["script import time; time.sleep(120)"],
        )
        with session.configure(launch_args):
            pass
        session.verify_stopped_on_entry()

        start = time.monotonic()
        session.disconnect(terminateDebuggee=True)
        elapsed = time.monotonic() - start

        # The bounded step timeout in DAP.cpp is 2s each for the process wait
        # and the terminate commands wait. Give some slack for build variance,
        # but fail well before the 50s DAP client timeout.
        self.assertLess(
            elapsed,
            10.0,
            f"disconnect took {elapsed:.1f}s; expected to be bounded",
        )
