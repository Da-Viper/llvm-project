"""
Test SBTarget::FindContexts / SBModule::FindContexts APIs.

These wrap `Module::ResolveSymbolContextsForFileSpec` and give clients access
to the same file+line resolution machinery `BreakpointResolverFileLine` uses,
including inlined instances -- without having to create a real breakpoint.
"""

import lldb
from lldbsuite.test.decorators import *
from lldbsuite.test.lldbtest import *
from lldbsuite.test import lldbutil


def line_entry(file_spec: lldb.SBFileSpec, line: int):
    entry = lldb.SBLineEntry()
    entry.SetFileSpec(file_spec)
    entry.SetLine(line)
    return entry


class FindContextsAPITestCase(TestBase):

    def test_find_contexts_finds_inlined_header_entry(self):
        """
        `FindContexts` should return the inlined instance of a header-defined
        function even though the header is not the caller's compile unit.
        The pre-existing `SBCompileUnit::GetLineEntryAtIndex` walk cannot see
        those entries.
        """
        self.build()
        (target, process, thread, bkpt) = lldbutil.run_to_source_breakpoint(
            self, "// break here", lldb.SBFileSpec("main.cpp")
        )
        self.assertTrue(target, VALID_TARGET)

        header_spec = lldb.SBFileSpec("inlined.h")
        inlined_line = line_number("inlined.h", "// inlined body")

        # With check_inlines=True the header's line entry is reachable across
        # the whole target -- this is the case that motivated the API.
        sc_list: lldb.SBSymbolContextList = target.FindContexts(
            line_entry(header_spec, inlined_line), True
        )
        self.assertGreater(
            sc_list.GetSize(),
            0,
            "FindContexts(check_inlines=True) should find the inlined instance",
        )

        # Every context should carry a line entry actually on the requested
        # line, and its file should match the header.
        for i, sc in enumerate(sc_list):
            entry = sc.GetLineEntry()
            self.assertTrue(entry.IsValid(), f"context {i} missing line entry")
            self.assertEqual(entry.GetLine(), inlined_line)
            self.assertEqual(entry.GetFileSpec().GetFilename(), "inlined.h")

    def test_find_contexts_check_inlines_false(self):
        """
        With check_inlines=False, the header's line entry is only returned if
        the header itself is a primary compile-unit file. In our fixture it
        isn't -- so we expect zero contexts.
        """
        self.build()
        (target, process, thread, bkpt) = lldbutil.run_to_source_breakpoint(
            self, "// break here", lldb.SBFileSpec("main.cpp")
        )
        header_spec = lldb.SBFileSpec("inlined.h")
        inlined_line = line_number("inlined.h", "// inlined body")

        sc_list = target.FindContexts(line_entry(header_spec, inlined_line), False)
        self.assertEqual(
            sc_list.GetSize(),
            0,
            "FindContexts(check_inlines=False) should not surface header inlines",
        )

    def test_find_contexts_primary_source(self):
        """
        For a line in a primary compile-unit file, both `check_inlines` values
        should return a non-empty result. Basic smoke test.
        """
        self.build()
        (target, process, thread, bkpt) = lldbutil.run_to_source_breakpoint(
            self, "// break here", lldb.SBFileSpec("main.cpp")
        )
        main_spec = lldb.SBFileSpec("main.cpp")
        break_line = line_number("main.cpp", "// break here")

        for check_inlines in (True, False):
            sc_list = target.FindContexts(
                line_entry(main_spec, break_line), check_inlines
            )
            self.assertGreater(
                sc_list.GetSize(),
                0,
                f"FindContexts on primary source should not be empty "
                f"(check_inlines={check_inlines})",
            )

    def test_find_contexts_matches_between_target_and_module(self):
        """
        `SBTarget::FindContexts` should return the same entries as
        `SBModule::FindContexts` on the module containing the match, for a
        single-module executable.
        """
        self.build()
        (target, process, thread, bkpt) = lldbutil.run_to_source_breakpoint(
            self, "// break here", lldb.SBFileSpec("main.cpp")
        )

        main_spec = lldb.SBFileSpec("main.cpp")
        break_line = line_number("main.cpp", "// break here")
        source_location = line_entry(main_spec, break_line)

        target_list = target.FindContexts(source_location)
        self.assertGreater(target_list.GetSize(), 0)

        # The a.out module owns main.cpp; its FindContexts should give the
        # same number of matches for the same source location.
        module = target.FindModule(lldb.SBFileSpec("a.out"))
        self.assertTrue(module.IsValid())
        module_list = module.FindContexts(source_location)
        self.assertEqual(target_list.GetSize(), module_list.GetSize())

    def test_find_contexts_invalid_inputs(self):
        """
        Invalid file spec or missing line should yield empty results, not
        crash.
        """
        self.build()
        (target, process, thread, bkpt) = lldbutil.run_to_source_breakpoint(
            self, "// break here", lldb.SBFileSpec("main.cpp")
        )

        empty_result = target.FindContexts(line_entry(lldb.SBFileSpec(), 1))
        self.assertEqual(empty_result.GetSize(), 0)

        missing_line = target.FindContexts(
            line_entry(lldb.SBFileSpec("main.cpp"), 99999)
        )
        self.assertEqual(missing_line.GetSize(), 0)

    def test_find_contexts_invalid_line_entry(self):
        """
        A default-constructed `SBLineEntry` (no file spec, no line) is
        invalid -- `FindContexts` should short-circuit and return empty.
        """
        self.build()
        (target, process, thread, bkpt) = lldbutil.run_to_source_breakpoint(
            self, "// break here", lldb.SBFileSpec("main.cpp")
        )

        empty_entry = lldb.SBLineEntry()
        self.assertFalse(empty_entry.IsValid())
        self.assertEqual(target.FindContexts(empty_entry).GetSize(), 0)
