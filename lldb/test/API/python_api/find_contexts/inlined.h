#ifndef INLINED_H
#define INLINED_H

// The body of this inline function lives in this header. When the caller
// gets it inlined, the header's line entries should still show up in the
// module's line table via `Module::ResolveSymbolContextsForFileSpec` with
// `check_inlines=true` -- but they will NOT appear in `caller.cpp`'s
// SBCompileUnit line-entry index. That difference is what
// SBTarget::FindContexts / SBModule::FindContexts exists to bridge.
inline int inlined_add(int a, int b) __attribute__((always_inline));
inline int inlined_add(int a, int b) {
  int sum = a + b; // inlined body
  return sum;
}

#endif
