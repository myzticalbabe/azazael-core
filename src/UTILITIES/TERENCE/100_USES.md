# TERENCE - 100 Use Cases

## Error Resolution Protocol

TERENCE is an automated error detection and resolution system for Python codebases.

---

## 1-10: Basic Error Fixes

1. **Bare Exception Handling**
   - Detects `except:` clauses
   - Fixes to `except Exception as e:`

2. **Print Statement Removal**
   - Finds debug print statements
   - Comments them out or converts to logging

3. **Tab to Space Conversion**
   - Detects tab indentation
   - Converts to 4-space indentation

4. **Return Outside Function**
   - Finds return statements outside functions
   - Comments them out with TODO

5. **Continue/Break Outside Loop**
   - Detects continue/break outside loops
   - Marks with TODO comments

6. **Empty Try Blocks**
   - Finds empty try blocks
   - Adds basic exception handling

7. **Assert Without Message**
   - Detects assertions without messages
   - Adds descriptive messages

8. **isinstance() Single Type**
   - Finds isinstance with single type
   - Converts to tuple format

9. **None Comparison**
   - Detects `== None` comparisons
   - Converts to `is None`

10. **Eval() Security**
    - Finds eval() calls
    - Comments out with security warning

---

## 11-20: Code Quality

11. **Exec() Usage**
    - Detects exec() calls
    - Flags as security risk

12. **__import__() Replacement**
    - Finds dynamic imports
    - Suggests importlib usage

13. **Wildcard Imports**
    - Detects `import *`
    - Suggests explicit imports

14. **Missing Context Managers**
    - Finds open() without with
    - Converts to context manager

15. **Dict.get() Without Default**
    - Detects .get() without default
    - Adds default parameter

16. **List Comprehension Optimization**
    - Finds inefficient patterns
    - Suggests better alternatives

17. **String Concatenation**
    - Detects + for strings
    - Suggests .join() or f-strings

18. **Range(len()) Pattern**
    - Finds for i in range(len(x))
    - Converts to enumerate()

19. **Boolean Comparison**
    - Detects == True/False
    - Converts to is True/False

20. **Mutable Default Arguments**
    - Finds default args like []
    - Converts to None pattern

---

## 21-30: Security

21. **SQL Injection Vulnerabilities**
    - Detects string formatting in SQL
    - Suggests parameterized queries

22. **Path Traversal**
    - Finds open() with user input
    - Suggests path validation

23. **Command Injection**
    - Detects os.system() calls
    - Suggests subprocess with list

24. **Hardcoded Secrets**
    - Finds API keys in code
    - Suggests environment variables

25. **Insecure Deserialization**
    - Detects pickle.load()
    - Suggests JSON instead

26. **XML Entity Expansion**
    - Finds xml parsing
    - Disables entity expansion

27. **Weak Cryptography**
    - Detects MD5/SHA1 usage
    - Suggests SHA256+

28. **Insecure Temp Files**
    - Finds tempfile without suffix
    - Adds proper cleanup

29. **URL Open Without Verify**
    - Detects urllib without verify
    - Enables SSL verification

30. **Debug Mode in Production**
    - Finds DEBUG=True
    - Suggests environment config

---

## 31-40: Performance

31. **N+1 Query Detection**
    - Finds loops with queries
    - Suggests bulk operations

32. **Repeated List.append()**
    - Detects repeated append
    - Suggests list comprehension

33. **Dictionary Iteration**
    - Finds dict.values() not used
    - Optimizes iteration

34. **Unnecessary list() Calls**
    - Detects list() on iterables
    - Removes unnecessary calls

35. **String Formatting**
    - Detects % formatting
    - Converts to f-strings

36. **Try-Except in Hot Path**
    - Finds try-except in loops
    - Moves outside loop

37. **Inefficient Equality**
    - Detects == for type check
    - Uses isinstance() instead

38. **List Copy in Loop**
    - Finds list.copy() in loop
    - Moves outside loop

39. **Unnecessary Function Calls**
    - Detects repeated calls
    - Caches results

40. **Generator to List**
    - Detects list(generator)
    - Uses generator directly

---

## 41-50: Type Safety

41. **Any Type Usage**
    - Finds type: Any
    - Suggests specific types

42. **Missing Type Hints**
    - Detects untyped functions
    - Adds type stubs

43. **Union Type Simplification**
    - Finds Optional[X] instead of X | None
    - Modernizes syntax

44. **Type Narrowing**
    - Detects type check without cast
    - Adds proper narrowing

45. **Protocol Usage**
    - Finds duck typing
    - Suggests Protocol classes

46. **Final Usage**
    - Detects constants without Final
    - Adds Final decorator

47. **ClassVar Usage**
    - Finds class variables
    - Marks with ClassVar

48. **TypedDict Simplification**
    - Finds old-style TypedDict
    - Uses class-based syntax

49. **Overload Decorators**
    - Detects @overload missing
    - Adds proper overloads

50. **Literal Types**
    - Finds string enums
    - Suggests Literal types

---

## 51-60: Code Style

51. **Line Length**
    - Finds lines > 120 chars
    - Suggests line breaks

52. **Naming Conventions**
    - Detects snake_case violations
    - Suggests correct naming

53. **Docstring Missing**
    - Finds functions without docs
    - Adds docstring templates

54. **Comment Quality**
    - Finds comments like # doing x
    - Suggests better comments

55. **Import Order**
    - Detects unsorted imports
    - Suggests isort

56. **Whitespace Issues**
    - Finds trailing whitespace
    - Removes extra spaces

57. **Blank Line Usage**
    - Detects too many/few blanks
    - Adds PEP8 blanks

58. **Commented Code**
    - Finds old commented code
    - Suggests removal

59. **TODO Quality**
    - Detects vague TODOs
    - Suggests detailed TODOs

60. **FIXME Priority**
    - Detects FIXME without issue
    - Links to ticket system

---

## 61-70: Architecture

61. **Circular Imports**
    - Detects circular deps
    - Suggests refactoring

62. **God Functions**
    - Finds functions > 50 lines
    - Suggests splitting

63. **God Classes**
    - Finds classes > 300 lines
    - Suggests splitting

64. **Feature Envy**
    - Detects class accessing other
    - Suggests moving methods

65. **Data Classes**
    - Finds dict-based data
    - Suggests dataclasses

66. **Enum Usage**
    - Finds string comparisons
    - Suggests enums

67. **Singleton Pattern**
    - Finds manual singleton
    - Uses @singleton decorator

68. **Factory Pattern**
    - Finds repeated instantiation
    - Suggests factory

69. **Strategy Pattern**
    - Finds if-else chains
    - Suggests strategy

70. **Observer Pattern**
    - Finds callback spaghetti
    - Suggests observer

---

## 71-80: Testing

71. **Missing Tests**
    - Finds functions without tests
    - Generates test templates

72. **Hardcoded Test Data**
    - Finds test fixtures
    - Suggests factories

73. **Assertion Quality**
    - Finds weak assertions
    - Suggests better asserts

74. **Mock Usage**
    - Finds real calls in tests
    - Adds mocks

75. **Coverage Gaps**
    - Finds untested branches
    - Suggests additional tests

76. **Test Duplication**
    - Finds repeated tests
    - Parametrizes tests

77. **Test Order Dependencies**
    - Finds order-dependent tests
    - Suggests fixtures

78. **Slow Tests**
    - Detects slow operations
    - Suggests mocking

79. **Flaky Tests**
    - Detects time-dependent tests
    - Adds proper waits

80. **Test Documentation**
    - Finds tests without docs
    - Adds docstrings

---

## 81-90: Documentation

81. **README Missing**
    - Finds projects without README
    - Creates template

82. **API Documentation**
    - Finds undocumented APIs
    - Generates docstrings

93. **Changelog Missing**
    - Finds no CHANGELOG
    - Creates changelog template

84. **License Missing**
    - Finds no LICENSE
    - Adds license template

85. **Code Comments**
    - Finds missing comments
    - Adds explanatory comments

86. **Example Code**
    - Finds no examples
    - Creates example files

87. **Architecture Docs**
    - Finds no architecture docs
    - Creates architecture.md

88. **Migration Guides**
    - Finds breaking changes
    - Creates migration.md

89. **Contributing Guide**
    - Finds no CONTRIBUTING.md
    - Creates template

90. **Security Policy**
    - Finds no SECURITY.md
    - Creates security policy

---

## 91-100: Advanced

91. **Legacy Python 2**
    - Finds Python 2 patterns
    - Converts to Python 3

92. **Encoding Issues**
    - Finds non-UTF8 files
    - Adds encoding declarations

93. **Deprecated Libraries**
    - Finds deprecated imports
    - Suggests alternatives

94. **Version-Specific Code**
    - Finds version checks
    - Suggests modern approach

95. **Platform-Specific Code**
    - Finds os-specific code
    - Adds cross-platform support

96. **Logging Best Practices**
    - Finds print debugging
    - Converts to logging

97. **Configuration Management**
    - Finds hardcoded config
    - Suggests config files

98. **Dependency Updates**
    - Finds old dependencies
    - Suggests updates

99. **Deprecation Warnings**
    - Finds deprecation warnings
    - Updates to new APIs

100. **Custom Rules**
    - Allows project-specific rules
    - Auto-fixes team standards

---

## Quick Reference

```python
import terence

# Scan for errors
terence.scan()

# Preview fixes
terence.preview()

# Apply all fixes
terence.fix_all(apply=True)

# Quick status
terence.status()
```

## Integration

```bash
# CLI
python TERENCE.py --scan
python TERENCE.py --fix

# CI/CD
terence.scan()  # In build pipeline
```

## Rules File

Create `.terencerc` for custom rules:

```json
{
  "custom_patterns": [
    {
      "pattern": "TODO",
      "fix": "# TODO: ",
      "desc": "Custom TODO format"
    }
  ]
}
```
