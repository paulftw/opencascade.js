from filter.filterIncludeFiles import filterIncludeFile
from typing import Set
import os

occtBasePath = "/occt/src/"

def getGlobalIncludes() -> Set[str]:
  includeFiles = list()
  additionalIncludePaths = list()
  for dirpath, dirnames, filenames in os.walk(occtBasePath):
    additionalIncludePaths.append(str(dirpath))
    for item in filenames:
      if filterIncludeFile(item):
        includeFiles.append(str(os.path.join(dirpath, item)))
  return [includeFiles, additionalIncludePaths]

[ocIncludeFiles, ocIncludePaths] = getGlobalIncludes()

additionalIncludePaths = [
  "/rapidjson/include",
  "/freetype/include/freetype",
  "/freetype/include",
]

includePathArgs = \
  list(dict.fromkeys(map(lambda x: "-I" + x, ocIncludePaths))) + \
  list(map(lambda x: "-I" + x, [
    "/emsdk/upstream/emscripten/system/lib/libc/musl/arch/emscripten/",
    "/emsdk/upstream/emscripten/system/include/",
    "/emsdk/upstream/emscripten/system/lib/libcxx/include/",
    "/src/include/opencascade/",
    "/emsdk/upstream/lib/clang/" + next(os.walk('/emsdk/upstream/lib/clang/'))[1][0] + "/include/",
    "/emsdk/upstream/emscripten/system/lib/libcxx/include/__support/newlib/"
  ])) + \
  list(map(lambda x: "-I" + x, additionalIncludePaths))

print("Include paths:", includePathArgs)
  
ocIncludeStatements = os.linesep.join(map(lambda x: "#include \"" + os.path.basename(x) + "\"", list(sorted(ocIncludeFiles))))
