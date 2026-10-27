"""Shared connection leases prevent offline restoration while app/worker is open.

Locks are advisory on POSIX. All supported Sika connections cooperate; external
database editors must also be closed. The sidecar is never deleted or replaced.
"""
import os
from pathlib import Path

class DatabaseLease:
    def __init__(self,database,exclusive=False):
        self.path=Path(str(Path(database).resolve())+'.sika-lock')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600)
        self.file=os.fdopen(fd,'r+b');self.overlapped=None
        try:
            if os.name=='nt':
                import ctypes
                import msvcrt
                from ctypes import wintypes
                class Overlapped(ctypes.Structure):
                    _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),
                        ('Offset',wintypes.DWORD),('OffsetHigh',wintypes.DWORD),('hEvent',wintypes.HANDLE)]
                self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                self.kernel.LockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
                self.kernel.LockFileEx.restype=wintypes.BOOL
                self.kernel.UnlockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
                self.kernel.UnlockFileEx.restype=wintypes.BOOL
                self.handle=msvcrt.get_osfhandle(self.file.fileno());self.overlapped=Overlapped()
                if not self.kernel.LockFileEx(self.handle,1|(2 if exclusive else 0),0,1,0,ctypes.byref(self.overlapped)):
                    raise OSError(ctypes.get_last_error(),'Database is in use or maintenance is active.')
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        except BaseException:
            self.file.close();raise
    def close(self):
        if self.file.closed:return
        if os.name=='nt':
            import ctypes
            self.kernel.UnlockFileEx(self.handle,0,1,0,ctypes.byref(self.overlapped))
        else:
            import fcntl
            fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        self.file.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
