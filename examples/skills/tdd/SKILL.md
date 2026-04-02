---
name: tdd
description: Enforce test-driven development workflow
auto: false
trigger: tdd
---

Follow strict TDD workflow:

1. **RED**: Write a failing test first
   - Test should describe the desired behavior
   - Run it to confirm it fails
   - The failure message should be clear

2. **GREEN**: Write minimal code to pass
   - Only enough code to make the test pass
   - No extra features, no premature optimization

3. **REFACTOR**: Clean up
   - Remove duplication
   - Improve naming
   - Keep all tests passing

Repeat for each behavior. Never write production code without a failing test first.
