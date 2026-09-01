int helper(int x) { // helper
  return x + 1;
}

int main(int argc, char const *argv[]) {
  int var_1 = 10;
  var_1 = 20; // breakpoint 1

  // blank/comment-only line intentionally left below.

  int var_2 = 40; // goto target
  return 0;
}
