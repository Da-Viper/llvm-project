//===-- GotoTargetsRequestHandler.cpp -------------------------------------===//
//
// Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
// See https://llvm.org/LICENSE.txt for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
//
//===----------------------------------------------------------------------===//

#include "DAP.h"
#include "DAPError.h"
#include "Protocol/ProtocolRequests.h"
#include "RequestHandler.h"
#include "lldb/API/SBCompileUnit.h"
#include "lldb/API/SBFileSpec.h"
#include "lldb/API/SBLineEntry.h"
#include "lldb/API/SBSymbolContext.h"
#include "lldb/API/SBSymbolContextList.h"
#include "lldb/API/SBTarget.h"
#include "lldb/lldb-defines.h"
#include "lldb/lldb-enumerations.h"
#include "llvm/Support/Error.h"

using namespace lldb_dap::protocol;
using namespace lldb;

/// Return true if \a line_entry belongs to the same source file as \a
/// primary_file_spec. Compare the char* pointers into LLDB's constant string
/// pool the same way BreakpointLocationsRequestHandler does.
static bool LineEntryMatchesSource(const SBLineEntry &line_entry,
                                   const SBFileSpec &primary_file_spec) {
  SBFileSpec entry_spec = line_entry.GetFileSpec();
  return entry_spec.GetFilename() == primary_file_spec.GetFilename() &&
         entry_spec.GetDirectory() == primary_file_spec.GetDirectory();
}

/// Walk every compile unit that owns \a primary_file_spec and collect its
/// line entries in that file. Returned entries are in line-table order.
/// Used as the fallback source for the "nearest previous / next line" scan
/// when no exact match is found.
static std::vector<SBLineEntry>
CollectLineEntriesForFile(SBTarget target,
                          const SBFileSpec &primary_file_spec) {
  std::vector<SBLineEntry> candidates;
  SBSymbolContextList cu_list = target.FindCompileUnits(primary_file_spec);
  for (uint32_t c = 0; c < cu_list.GetSize(); ++c) {
    SBCompileUnit compile_unit = cu_list.GetContextAtIndex(c).GetCompileUnit();
    if (!compile_unit.IsValid())
      continue;
    const uint32_t num_entries = compile_unit.GetNumLineEntries();
    for (uint32_t i = 0; i < num_entries; ++i) {
      SBLineEntry entry = compile_unit.GetLineEntryAtIndex(i);
      if (!entry.IsValid())
        continue;
      if (entry.GetLine() == 0)
        continue;
      if (!LineEntryMatchesSource(entry, primary_file_spec))
        continue;
      candidates.push_back(entry);
    }
  }
  return candidates;
}

/// Build a target for the given resolved line entry and record it in the DAP
/// session so a subsequent `goto` request can look it up.
static GotoTarget MakeTarget(lldb_dap::DAP &dap,
                             const SBFileSpec &primary_file_spec,
                             const SBLineEntry &entry, llvm::StringRef prefix) {
  const uint64_t id = dap.goto_targets.Insert(entry);
  const uint32_t line = entry.GetLine();

  const llvm::StringRef filename = primary_file_spec.GetFilename();
  GotoTarget target;
  target.id = id;
  if (prefix.empty())
    target.label = llvm::formatv("{0}:{1}", filename, line);
  else
    target.label = llvm::formatv("{0}: {1}:{2}", prefix, filename, line);
  target.line = line;
  const uint32_t column = entry.GetColumn();
  if (column != LLDB_INVALID_COLUMN_NUMBER)
    target.column = column;
  return target;
}

namespace lldb_dap {
/// This request retrieves the possible goto targets for the specified source
/// location. These targets can be used in the `goto` request. Clients should
/// only call this request if the corresponding capability
/// `supportsGotoTargetsRequest` is true.
llvm::Expected<GotoTargetsResponseBody>
GotoTargetsRequestHandler::Run(const GotoTargetsArguments &args) const {
  if (!args.source.path)
    return llvm::make_error<DAPError>("source path is required");

  const SBFileSpec primary_file_spec(args.source.path->c_str(), true);
  if (!primary_file_spec.IsValid())
    return llvm::make_error<DAPError>("invalid source path");

  GotoTargetsResponseBody body;

  // Try an exact-line match via the resolver-backed SBAPI. This handles
  // inlined-function line entries whose entries live in a different compile
  // unit than the source file's primary CU (e.g. `.../stl/vector:123`).
  SBLineEntry source;
  source.SetFileSpec(primary_file_spec);
  source.SetLine(args.line);
  SBSymbolContextList contexts =
      dap.target.FindSymbolContexts(source, lldb::eSymbolContextLineEntry);
  for (uint32_t i = 0; i < contexts.GetSize(); ++i) {
    SBLineEntry entry = contexts.GetContextAtIndex(i).GetLineEntry();
    if (!entry.IsValid())
      continue;
    body.targets.push_back(MakeTarget(dap, primary_file_spec, entry, ""));
    return body;
  }

  // No exact match -- offer the nearest previous and next valid lines in the
  // same file so the client can pick one before invoking `goto`.
  std::vector<SBLineEntry> candidates =
      CollectLineEntriesForFile(dap.target, primary_file_spec);
  if (candidates.empty())
    return body;

  // If the compile-unit walk *did* find a direct entry at the requested
  // line, prefer that as an exact match. This catches cases where the
  // resolver's inline-context handling filtered the entry out.
  for (const SBLineEntry &entry : candidates) {
    if (entry.GetLine() == args.line) {
      body.targets.push_back(MakeTarget(dap, primary_file_spec, entry, ""));
      return body;
    }
  }

  const SBLineEntry *prev_entry = nullptr;
  const SBLineEntry *next_entry = nullptr;
  for (const SBLineEntry &entry : candidates) {
    const uint32_t line = entry.GetLine();
    if (line < args.line) {
      if (!prev_entry || line > prev_entry->GetLine())
        prev_entry = &entry;
    } else if (line > args.line) {
      if (!next_entry || line < next_entry->GetLine())
        next_entry = &entry;
    }
  }

  if (prev_entry)
    body.targets.push_back(MakeTarget(dap, primary_file_spec, *prev_entry,
                                      /*prefix=*/"prev"));
  if (next_entry)
    body.targets.push_back(MakeTarget(dap, primary_file_spec, *next_entry,
                                      /*prefix=*/"next"));
  return body;
}

} // namespace lldb_dap
