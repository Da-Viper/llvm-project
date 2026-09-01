"""
Test lldb-dap goto and gotoTargets requests.
"""

from lldbsuite.test.decorators import *
from lldbsuite.test.lldbtest import line_number
from lldbsuite.test.tools.lldb_dap import DAPTestCaseBase
from lldbsuite.test.tools.lldb_dap.types import *


class TestDAP_goto(DAPTestCaseBase):
    def run_to_bp1(self):
        """Launch, stop at `// breakpoint 1`, return (session, source, stop_event)."""
        program = self.getBuildArtifact("a.out")
        source_path = self.getSourcePath("main.cpp")
        session = self.build_and_create_session()
        bp_line = line_number(source_path, "// breakpoint 1")
        with session.configure(LaunchArgs(program)) as ctx:
            [bp1] = session.resolve_source_breakpoints(source_path, [bp_line])
        stop_event = session.verify_stopped_on_breakpoint(bp1, after=ctx.process_event)
        return session, source_path, stop_event

    def test_goto_exact_line(self):
        """When the requested line has code, we get exactly one target on that line."""
        session, source_path, _stop_event = self.run_to_bp1()
        self.assertTrue(
            session.capabilities().supportsGotoTargetsRequest,
            "expect supportsGotoTargetsRequest capability",
        )
        target_line = line_number(source_path, "// goto target")

        targets_response = session.send_request(
            GotoTargetsArgs(source=Source(path=source_path), line=target_line)
        ).result("gotoTargets should succeed")
        targets = targets_response.body.targets
        self.assertEqual(len(targets), 1, f"expected one exact target, got {targets}")
        self.assertEqual(targets[0].line, target_line)
        session.continue_to_exit()

    def test_goto_snaps_to_next_and_previous(self):
        """
        When the requested line is blank/comment-only inside the function, we
        get two targets: nearest previous and nearest next valid line. Jumping
        via one of them succeeds and stops on the resolved line.
        """
        session, source_path, stop_event = self.run_to_bp1()
        thread_id = self.expect_not_none(stop_event.body.threadId)

        bp_line = line_number(source_path, "// breakpoint 1")
        target_line = line_number(source_path, "// goto target")
        blank_line = line_number(
            source_path, "// blank/comment-only line intentionally left below."
        )

        targets_response = session.send_request(
            GotoTargetsArgs(source=Source(path=source_path), line=blank_line)
        ).result("gotoTargets should succeed")
        targets = targets_response.body.targets
        self.assertEqual(
            len(targets),
            2,
            f"expected prev+next targets for blank line, got {targets}",
        )
        lines = sorted(t.line for t in targets)
        # The nearest previous valid line is the breakpoint line itself
        # (the line above the comment). The nearest next is the goto target.
        self.assertEqual(lines, [bp_line, target_line])

        # Pick the "next" target and jump to it; expect a stopped(goto) event
        # on the resolved line.
        next_target = next(t for t in targets if t.line == target_line)
        session.send_request(
            GotoArgs(threadId=thread_id, targetId=next_target.id)
        ).result("goto should succeed")
        stopped = session.wait_for_stopped_event(
            matching_any=[StoppedReason.GOTO], after=targets_response
        )
        top_frame = session.thread_context_from(stopped).top_frame().frame
        self.assertEqual(top_frame.line, target_line)
        session.continue_to_exit()

    def test_goto_across_functions_returns_target(self):
        """
        `gotoTargets` uses only the request's `source`+`line` — no thread or
        frame context — so a line in a different function of the same source
        is still a valid target. Whether the follow-up `goto` actually
        succeeds is enforced by `SBThread::JumpToLine`, not by this query.
        """
        session, source_path, _stop_event = self.run_to_bp1()
        # Line 1 is the `int helper(...)` definition, outside `main`.
        helper_line = 1

        targets_response = session.send_request(
            GotoTargetsArgs(source=Source(path=source_path), line=helper_line)
        ).result("gotoTargets should succeed")
        exact = [t for t in targets_response.body.targets if t.line == helper_line]
        self.assertGreater(
            len(exact),
            0,
            f"expected an exact target on line {helper_line} "
            f"(gotoTargets is thread-independent), got "
            f"{targets_response.body.targets}",
        )
        session.continue_to_exit()

    def test_goto_invalid_target(self):
        """Sending goto with a target id that was never handed out should fail."""
        session, _source_path, stop_event = self.run_to_bp1()
        thread_id = self.expect_not_none(stop_event.body.threadId)

        response = session.send_request(
            GotoArgs(threadId=thread_id, targetId=99999999)
        ).result_or_error()
        self.assertFalse(response.success, "expect goto to fail")
        session.continue_to_exit()

    def test_goto_actually_skips_execution(self):
        """
        Prove `goto` skipped code by observing a variable state combination
        that is only reachable if a specific line was not executed.

        Fixture:
            int var_1 = 10;
            var_1 = 20;      // breakpoint 1  -- stopped here, so var_1 == 10
            int var_2 = 40;  // goto target

        Normal flow from breakpoint 1 would produce var_1=20, var_2=40. If
        `goto` really jumped over the `var_1 = 20` line, then after stepping
        past the `int var_2 = 40` line we should see var_1=10, var_2=40 --
        an execution state unreachable by any other means.
        """
        session, source_path, stop_event = self.run_to_bp1()
        thread_id = self.expect_not_none(stop_event.body.threadId)
        target_line = line_number(source_path, "// goto target")

        # Sanity: the "var_1 = 20" line hasn't executed yet at the breakpoint.
        pre_var_1 = session.top_frame_from(stop_event).locals["var_1"].value_as_int
        self.assertEqual(pre_var_1, 10, "var_1 should still be 10 at breakpoint")

        # Ask for a target on the goto line and jump to it.
        targets_resp = session.send_request(
            GotoTargetsArgs(source=Source(path=source_path), line=target_line)
        ).result("gotoTargets should succeed")
        target = targets_resp.body.targets[0]
        session.send_request(GotoArgs(threadId=thread_id, targetId=target.id)).result(
            "goto should succeed"
        )
        goto_stopped = session.verify_stopped(StoppedReason.GOTO, after=targets_resp)
        self.assertEqual(session.top_frame_from(goto_stopped).frame.line, target_line)

        # Step over the `int var_2 = 40;` line so it executes.
        step_stopped = session.step_over(threadId=thread_id)

        top_frame = session.top_frame_from(step_stopped)
        post_var_1 = top_frame.locals["var_1"].value_as_int
        post_var_2 = top_frame.locals["var_2"].value_as_int
        # This combination is only reachable if the goto skipped `var_1 = 20;`.
        self.assertEqual(post_var_1, 10, "var_1 must still be 10 -- goto did not skip")
        self.assertEqual(post_var_2, 40, "var_2 should have been assigned 40")

        session.continue_to_exit()
