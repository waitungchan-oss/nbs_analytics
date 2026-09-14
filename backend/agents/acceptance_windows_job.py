"""Windows process containment helpers for acceptance shard cleanup."""

from __future__ import annotations


def _kernel32():
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle_type = ctypes.c_void_p
    bool_type = ctypes.c_int
    dword_type = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [handle_type]
    kernel32.CloseHandle.restype = bool_type
    return kernel32, handle_type, bool_type, dword_type


def process_alive(process_id: int) -> bool:
    """Read process state without sending a signal on Windows."""
    import ctypes

    kernel32, handle_type, bool_type, dword_type = _kernel32()
    kernel32.OpenProcess.argtypes = [dword_type, bool_type, dword_type]
    kernel32.OpenProcess.restype = handle_type
    kernel32.GetExitCodeProcess.argtypes = [handle_type, ctypes.POINTER(dword_type)]
    kernel32.GetExitCodeProcess.restype = bool_type
    handle = kernel32.OpenProcess(0x1000, 0, process_id)
    if not handle:
        return ctypes.get_last_error() != 87
    exit_code = dword_type()
    try:
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def create_job() -> int:
    import ctypes

    kernel32, handle_type, bool_type, dword_type = _kernel32()
    kernel32.CreateJobObjectW.argtypes = [handle_type, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = handle_type
    kernel32.SetInformationJobObject.argtypes = [handle_type, ctypes.c_int, ctypes.c_void_p, dword_type]
    kernel32.SetInformationJobObject.restype = bool_type

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", dword_type),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", dword_type),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", dword_type),
            ("SchedulingClass", dword_type),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise RuntimeError("windows job allocation failed")
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = 0x2000
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        raise RuntimeError("windows job configuration failed")
    return int(job)


def assign_process(job_handle: int, process_id: int) -> None:
    import ctypes

    kernel32, handle_type, bool_type, dword_type = _kernel32()
    kernel32.OpenProcess.argtypes = [dword_type, bool_type, dword_type]
    kernel32.OpenProcess.restype = handle_type
    kernel32.AssignProcessToJobObject.argtypes = [handle_type, handle_type]
    kernel32.AssignProcessToJobObject.restype = bool_type
    process_handle = kernel32.OpenProcess(0x1000 | 0x0100 | 0x0001, 0, process_id)
    if not process_handle:
        raise RuntimeError("windows process handle query failed")
    try:
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise RuntimeError("windows process job assignment failed")
    finally:
        kernel32.CloseHandle(process_handle)


def active_processes(job_handle: int) -> int:
    import ctypes

    kernel32, handle_type, bool_type, dword_type = _kernel32()
    kernel32.QueryInformationJobObject.argtypes = [
        handle_type, ctypes.c_int, ctypes.c_void_p, dword_type, ctypes.c_void_p
    ]
    kernel32.QueryInformationJobObject.restype = bool_type

    class AccountingInformation(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_longlong if name.endswith("Time") else dword_type)
            for name in (
                "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
                "ThisPeriodTotalKernelTime", "TotalPageFaultCount", "TotalProcesses",
                "ActiveProcesses", "TotalTerminatedProcesses",
            )
        ]
    if (
        ctypes.sizeof(AccountingInformation) != 48
        or AccountingInformation.TotalProcesses.offset != 36
        or AccountingInformation.ActiveProcesses.offset != 40
        or AccountingInformation.TotalTerminatedProcesses.offset != 44
    ):
        raise RuntimeError("windows job accounting layout is unsupported")
    info = AccountingInformation()
    if not kernel32.QueryInformationJobObject(job_handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
        raise RuntimeError("windows job liveness query failed")
    return int(info.ActiveProcesses)


def terminate_job(job_handle: int) -> None:
    import ctypes

    kernel32, handle_type, bool_type, dword_type = _kernel32()
    kernel32.TerminateJobObject.argtypes = [handle_type, dword_type]
    kernel32.TerminateJobObject.restype = bool_type
    kernel32.TerminateJobObject(job_handle, 1)


def close_job(job_handle: int) -> None:
    _kernel32()[0].CloseHandle(job_handle)
