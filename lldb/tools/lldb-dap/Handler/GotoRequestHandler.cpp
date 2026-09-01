//===-- GotoRequestHandler.cpp --------------------------------------------===//
//
// Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
// See https://llvm.org/LICENSE.txt for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
//
//===----------------------------------------------------------------------===//

#include "DAP.h"
#include "DAPError.h"
#include "LLDBUtils.h"
#include "Protocol/ProtocolEvents.h"
#include "Protocol/ProtocolRequests.h"
#include "RequestHandler.h"
#include "lldb/API/SBError.h"
#include "lldb/API/SBFileSpec.h"
#include "lldb/API/SBLineEntry.h"
#include "lldb/API/SBThread.h"
#include "llvm/Support/Error.h"
#include <optional>

namespace lldb_dap {

/// The request sets the location where the debuggee will continue to run.
/// This makes it possible to skip the execution of code or to execute code
/// again.
/// The code between the current location and the goto target is not executed
/// but skipped.
/// The debug adapter first sends the response and then a `stopped` event with
/// reason `goto`.
/// Clients should only call this request if the corresponding capability
/// `supportsGotoTargetsRequest` is true (because only then goto targets exist
/// that can be passed as arguments).
llvm::Error GotoRequestHandler::Run(const protocol::GotoArguments &args) const {
  if (dap.ProcessIsNotStopped())
    return llvm::make_error<NotStoppedError>();

  lldb::SBThread thread = dap.GetLLDBThread(args.threadId);
  if (!thread.IsValid())
    return llvm::make_error<DAPError>(
        llvm::formatv("invalid thread {}", args.threadId));

  std::optional<lldb::SBLineEntry> entry_opt =
      dap.goto_targets.GetLineEntry(args.targetId);
  if (!entry_opt)
    return llvm::make_error<DAPError>(
        llvm::formatv("invalid goto target {}", args.targetId));

  const lldb::SBLineEntry &entry = entry_opt.value();
  lldb::SBFileSpec file_spec = entry.GetFileSpec();
  uint32_t line = entry.GetLine();
  // TODO: pass column too, once SBThread::JumpToLine (or a successor)
  // accepts a column argument.

  lldb::SBError error = thread.JumpToLine(file_spec, line);
  if (error.Fail())
    return ToError(error);

  protocol::StoppedEventBody body;
  body.reason = protocol::eStoppedReasonGoto;
  body.threadId = thread.GetThreadID();
  dap.Send(protocol::Event{"stopped", std::move(body)});

  return llvm::Error::success();
}

} // namespace lldb_dap
