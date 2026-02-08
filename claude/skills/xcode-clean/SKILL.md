---
name: xcode-clean
description: Clean Xcode build artifacts and DerivedData
allowed-tools: [Bash, Read]
---

# Xcode Clean

Clean build artifacts, DerivedData, and module cache to resolve build corruption issues.

## When to Use

- Build fails with cryptic errors
- "Module compiled with Swift X but imported with Swift Y"
- Incremental build producing wrong results
- DerivedData corruption suspected
- Before fresh build to ensure clean state
- Disk space cleanup

## Process

### 1. Navigate to Project Directory
```bash
cd /Users/ryders/Documents/GitHub/SpuriousTime/SpuriousTime-macOS
```

### 2. Run Xcode Clean
```bash
xcodebuild clean \
           -project SpuriousTime-macOS.xcodeproj \
           -scheme SpuriousTime-macOS \
           -configuration Debug
```

**Timeout**: 60 seconds

### 3. Remove DerivedData (Optional - when corruption suspected)
```bash
# Find DerivedData folder for this project
DERIVED_DATA=$(find ~/Library/Developer/Xcode/DerivedData \
               -name "SpuriousTime-macOS-*" \
               -type d 2>/dev/null | head -1)

if [ -n "$DERIVED_DATA" ]; then
  echo "Removing DerivedData: $DERIVED_DATA"
  rm -rf "$DERIVED_DATA"
fi
```

### 4. Clear Module Cache (Optional - for module import issues)
```bash
rm -rf ~/Library/Developer/Xcode/DerivedData/ModuleCache.noindex
```

## Clean Levels

### Level 1: Basic Clean (Default)
```
- Remove build products
- Clear intermediate files
- Preserve DerivedData folder structure
- Fastest (10-15 seconds)
```

### Level 2: Deep Clean (DerivedData removal)
```
- Everything from Level 1
- Remove entire DerivedData folder
- Next build will be from scratch
- Slower rebuild (2-3 minutes)
- Use when: Module import errors, build corruption
```

### Level 3: Nuclear Clean (Module cache too)
```
- Everything from Level 2
- Clear shared module cache
- Affects all Xcode projects
- Use when: "Swift module compiled with..." errors
```

## Output Format

**Basic clean success**:
```
✅ CLEAN COMPLETED

Level: Basic
Duration: 12.3s
Cleaned: Build products, intermediate files

DerivedData: Preserved
Module Cache: Preserved

Next: Run /xcode-build for fresh build
```

**Deep clean success**:
```
✅ DEEP CLEAN COMPLETED

Level: Deep
Duration: 15.8s
Cleaned:
- Build products
- Intermediate files
- DerivedData folder (1.2 GB freed)

Next build will be from scratch (slower)
Next: Run /xcode-build
```

**Nuclear clean success**:
```
✅ NUCLEAR CLEAN COMPLETED

Level: Nuclear
Duration: 18.4s
Cleaned:
- Build products
- Intermediate files
- DerivedData folder
- Module cache (234 MB freed)

⚠️ All Xcode projects will rebuild modules on next build

Next: Run /xcode-build
```

## When to Use Each Level

| Symptom | Recommended Level |
|---------|-------------------|
| Generic build errors | Level 1: Basic Clean |
| "Module X not found" | Level 2: Deep Clean |
| "Module compiled with Swift 5.9 but imported with Swift 5.10" | Level 3: Nuclear Clean |
| Incremental build broken | Level 2: Deep Clean |
| Just want to free disk space | Level 2: Deep Clean |
| Build succeeds but app behaves oddly | Level 2: Deep Clean |

## Error Handling

**Clean fails**:
```
❌ CLEAN FAILED

Error: xcodebuild clean returned non-zero exit code

Possible causes:
- Xcode project file corrupted
- Scheme not found
- File permissions issue

Try: Manually delete DerivedData folder
```

**Permission denied**:
```
❌ PERMISSION DENIED

Could not delete DerivedData folder.

Fix:
sudo rm -rf ~/Library/Developer/Xcode/DerivedData/SpuriousTime-macOS-*
```

## Examples

**Example 1: Basic clean before fresh build**
```
User: "Clean and rebuild"
Assistant: *Invokes xcode-clean skill (Level 1)*

✅ CLEAN COMPLETED (12.3s)

*Invokes xcode-build skill*

✅ BUILD SUCCEEDED (145.6s)
Fresh build complete.
```

**Example 2: Module import errors**
```
User: "Build is failing with 'No such module SpuriousCore'"
Assistant: This suggests DerivedData corruption.

*Invokes xcode-clean skill (Level 2: Deep)*

✅ DEEP CLEAN COMPLETED
Freed 1.2 GB

*Invokes xcode-build skill*

✅ BUILD SUCCEEDED
Module imports resolved.
```

**Example 3: Swift version mismatch**
```
User: "Getting 'Module compiled with Swift 5.9' error"
Assistant: This requires nuclear clean.

*Invokes xcode-clean skill (Level 3: Nuclear)*

✅ NUCLEAR CLEAN COMPLETED
Module cache cleared

⚠️ Next build will recompile all modules (3-5 minutes)

*Invokes xcode-build skill*

✅ BUILD SUCCEEDED (247.3s)
All modules recompiled with correct Swift version.
```

## Success Criteria

- ✅ Clean command executed successfully
- ✅ Appropriate clean level chosen based on symptoms
- ✅ Disk space freed (reported in summary)
- ✅ Clear indication of what was cleaned
- ✅ Warning if clean will slow next build

## Notes

- Basic clean is fast but may not resolve all issues
- Deep clean removes DerivedData - safe and recommended for corruption
- Nuclear clean affects all Xcode projects - use sparingly
- Cleaned artifacts will be regenerated on next build
- DerivedData can grow to several GB over time
- Clean before committing to ensure reproducible builds
