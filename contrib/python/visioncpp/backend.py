from __future__ import print_function

import visioncpp as vp

import logging
import os
import sys
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import numpy as np

from ctypes import cdll
from labm8 import cache
from labm8 import fs
from labm8 import types
from pkg_resources import resource_filename
from shutil import rmtree
from subprocess import Popen, PIPE, STDOUT
from tempfile import mkdtemp


def get_host_cflags():
    """
    Get compilation cflags.

    Returns:
        str[]: List of compiler flags.
    """
    return [
        "-x", "c++",
        "-std=c++11",
        # Workaround for issue with libc++
        "-D_GLIBCXX_USE_CXX11_ABI=0",
        "-I" + os.path.join(vp.computecpp_prefix, "include"),
        "-I" + resource_filename(__name__, os.path.join("lib", "include")),
    ]


def get_device_cflags():
    """
    Get integration header compilation cflags.

    Returns:
        str[]: List of compiler flags.
    """
    proc = Popen([
        os.path.join(vp.computecpp_prefix, "bin", "computecpp_info"),
        "--dump-device-compiler-flags"
    ], stdout=PIPE, stderr=PIPE)
    stdout, _ = proc.communicate()
    return stdout.decode("utf-8").split()


def get_ldflags():
    """
    Get link flags.

    Returns:
        str[]: List of link flags.
    """
    libdirs = [os.path.join(vp.computecpp_prefix, "lib")]
    libs = ["ComputeCpp", "pthread"]
    return ["-L" + x for x in libdirs] + ["-l" + x for x in libs]


def invoke_computecpp(args, stdin=None):
    """
    Invoke ComputeCpp compute++ compiler.

    Arguments:
        args (str[]): Arguments.
        stdin (str, optional): Compiler input.
    """
    if stdin is not None:
        stdin = stdin.encode("utf-8")

    computecpp = os.path.join(vp.computecpp_prefix, "bin", "compute++")
    cmd = [computecpp] + args

    logging.info(" ".join(cmd))

    process = Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    stdout, stderr = process.communicate(stdin)

    if process.returncode != 0:
        print("================", file=sys.stderr)
        print("ComputeCpp Error", file=sys.stderr)
        print("================", file=sys.stderr)
        print(file=sys.stderr)
        if stdin is not None:
            print("=== compute++ input:", file=sys.stderr)
            print(file=sys.stderr)
            print(stdin.decode("utf-8"), file=sys.stderr, end="")
            print(file=sys.stderr)
        if stdout is not None:
            print("=== compute++ output:", file=sys.stderr)
            print(file=sys.stderr)
            print(stdout.decode("utf-8"), file=sys.stderr, end="")
            print(file=sys.stderr)
        if stderr is not None:
            print("=== compute++ error output:", file=sys.stderr)
            print(file=sys.stderr)
            print(stderr.decode("utf-8"), file=sys.stderr, end="")
            print(file=sys.stderr)
        print("=========================", file=sys.stderr)
        print("FATAL ERROR. TERMINATING.", file=sys.stderr)
        sys.exit(process.returncode)


def host_compile(code, stub, dir="/tmp"):
    """
    Compile object file.

    Arguments:
        code (str): C++ code.
        stub (str): Path to integration header.
        dir (str, optional): Output directory.

    Returns:
        str: Path to compiled object file.
    """
    dest = os.path.join(dir, "host.o")
    assert(not os.path.exists(dest))

    args = get_host_cflags() + [
        "-c", "-",
        "-fPIC",
        "-include", stub,
        "-o", dest
    ]

    invoke_computecpp(args, stdin=code)
    assert(os.path.exists(dest))
    return dest


def stub_file(code, dir="/tmp"):
    """
    Compile integration header.

    Arguments:
        code (str): C++ code.
        dir (str, optional): Output directory.

    Returns:
        str: Path to compiled integration header file.
    """
    dest = os.path.join(dir, "stub.sycl")
    assert(not os.path.exists(dest))

    args = get_host_cflags() + get_device_cflags() + [
        "-c", "-",
        "-o", dest
    ]

    invoke_computecpp(args, stdin=code)
    assert(os.path.exists(dest))
    return dest


def link(host, dir="/tmp"):
    """
    Link object file to library.

    Arguments:
        host (str): Path to object file.
        dir (str, optional): Output directory.

    Returns:
        str: Path to generated executable.
    """
    dest = os.path.join(dir, "visioncpp_native")
    assert(not os.path.exists(dest))

    computecpp_lib_path = os.path.join(vp.computecpp_prefix, "lib")

    args = [
        "-std=c++11",
        "-shared",
        "-Wl,-soname,visioncpp_native.so",
        "-Wl,-rpath=" + computecpp_lib_path,
        host,
        "-o", dest
    ] + get_ldflags()

    invoke_computecpp(args)
    assert(os.path.exists(dest))
    return dest


def check_for_computecpp():
    """
    Check that ComputeCpp files exist.

    Raises:
            VisionCppException: If ComputeCpp file(s) are mising.
    """
    def must_exist(path):
        """
        Check that a file exists.

        Arguments:
            path (str): Path to file.

        Returns:
            str: Path to file (same as argument).

        Raises:
            VisionCppException: If file does not exist.
        """
        if not os.path.exists(path):
            raise vp.VisionCppException(
                "file '{}' not found. Is ComputeCpp installed?"
                .format(path))
        return path

    must_exist(os.path.join(vp.computecpp_prefix, "bin", "computecpp_info"))
    must_exist(os.path.join(vp.computecpp_prefix, "bin", "compute++"))
    must_exist(os.path.join(vp.computecpp_prefix, "lib", "libComputeCpp.so"))


def compile_cpp_code(code):
    """
    Compile C++ code to a dynamic library.

    Arguments:
        code (str): C++ socde.

    Returns:
        str: Path to binary.
    """
    bincache = cache.FSCache(fs.path("~/.cache/visioncpp"))

    if bincache.get(code):
        logging.info("Found cached binary {}"
                     .format(fs.basename(bincache[code])))
    else:
        check_for_computecpp()

        counter = {"val": 0}

        def progress(msg):
            text = "{}: {}".format(counter["val"], msg) if msg else ""
            if logging.getLogger().getEffectiveLevel() <= logging.INFO:
                end = "\n"
            else:
                end = ""
            print("\r\033[K {}".format(text), end=end)
            counter["val"] += 1
            sys.stdout.flush()

        tmpdir = mkdtemp(prefix="visioncpp-")
        try:
            progress("compiling device code ...")
            stub = stub_file(code, dir=tmpdir)
            progress("compiling host code ...")
            host = host_compile(code, stub, dir=tmpdir)
            progress("linking executable ...")
            tmpbin = link(host, dir=tmpdir)
            progress("")

            bincache[code] = tmpbin
        except Exception as e:
            rmtree(tmpdir)
            raise e
        rmtree(tmpdir)

    return bincache[code]


def run(pipeline, binary):
    """
    Execute a program binary.

    Arguments:
        pipeline (list of visioncpp.Operation): Serialized pipeline.
        binary (str): Path to binary.

    Raises:
        TypeError: If any of the arguments are bad.
        ValueError: If binary does not exist.
        RuntimeError: If native tree returns non-zero exit status.
    """
    if not all(isinstance(stage, vp.Operation) for stage in pipeline):
        raise TypeError
    if not types.is_str(binary):
        raise TypeError
    if not (binary and os.path.exists(binary)):
        raise ValueError

    # Get input images
    impaths = []
    for stage in pipeline:
        if isinstance(stage, vp.Image):
            impaths.append(stage.input)

    lib = cdll.LoadLibrary(binary)

    # inputs
    assert(len(impaths) == 1)
    img = mpimg.imread(impaths[0])
    in1 = np.require(img, np.uint8, ['CONTIGUOUS', 'ALIGNED'])

    # output
    out_terminal = pipeline[-1]
    if not isinstance(out_terminal, vp.show):
        raise TypeError
    size = out_terminal.input.width * out_terminal.input.height * 3
    out = np.empty_like(np.zeros(size, dtype=np.uint8))

    lib.native_expression_tree.argtypes = [
        np.ctypeslib.ndpointer(np.uint8, flags='aligned, contiguous'),
        np.ctypeslib.ndpointer(np.uint8, flags='aligned, contiguous'), # output
    ]

    ret = lib.native_expression_tree(in1, out)
    if ret:
        raise RuntimeError("native expression tree failed")

    img = np.reshape(out, (-1, out_terminal.input.width, 3))
    plt.imshow(img)
    plt.show()
