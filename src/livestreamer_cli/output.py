import os
import shlex
import subprocess
import sys

from time import sleep

from .compat import is_win32, stdout
from .constants import DEFAULT_PLAYER_ARGUMENTS
from .utils import ignored

if is_win32:
    import msvcrt


class Output(object):
    def __init__(self):
        self.opened = False

    def open(self):
        self._open()
        self.opened = True

    def close(self):
        if self.opened:
            self._close()

        self.opened = False

    def write(self, data):
        if not self.opened:
            raise IOError("Output is not opened")

        return self._write(data)

    def _open(self):
        pass

    def _close(self):
        pass

    def _write(self, data):
        pass


class FileOutput(Output):
    def __init__(self, filename=None, fd=None):
        self.filename = filename
        self.fd = fd

    def _open(self):
        if self.filename:
            self.fd = open(self.filename, "wb")

        if is_win32:
            msvcrt.setmode(self.fd.fileno(), os.O_BINARY)

    def _close(self):
        if self.fd is not stdout:
            self.fd.close()

    def _write(self, data):
        self.fd.write(data)


class PlayerOutput(Output):
    def __init__(self, cmd, args=DEFAULT_PLAYER_ARGUMENTS,
                 filename=None, quiet=True, kill=True,
                 call=False, http=False, namedpipe=None):
        self.cmd = cmd
        self.args = args
        self.kill = kill
        self.call = call
        self.quiet = quiet

        self.filename = filename
        self.namedpipe = namedpipe
        self.http = http

        if self.namedpipe or self.filename or self.http:
            self.stdin = sys.stdin
        else:
            self.stdin = subprocess.PIPE

        if self.quiet:
            self.stdout = open(os.devnull, "w")
            self.stderr = open(os.devnull, "w")
        else:
            self.stdout = sys.stdout
            self.stderr = sys.stderr

    @property
    def running(self):
        sleep(0.5)
        self.player.poll()
        return self.player.returncode is None

    def _create_arguments(self):
        if self.namedpipe:
            filename = self.namedpipe.path
        elif self.filename:
            filename = self.filename
        elif self.http:
            filename = self.http.url
        else:
            filename = "-"

        args = self.args.format(filename=filename)
        cmd = self.cmd
        if is_win32:
            # We want to keep the backslashes on Windows as forcing the user to
            # escape backslashes for paths would be inconvenient.
            cmd = cmd.replace("\\", "\\\\")
            args = args.replace("\\", "\\\\")

        return shlex.split(cmd) + shlex.split(args)

    def _open(self):
        try:
            if self.call and self.filename:
                self._open_call()
            else:
                self._open_subprocess()
        finally:
            if self.quiet:
                # Output streams no longer needed in parent process
                self.stdout.close()
                self.stderr.close()

    def _open_call(self):
        subprocess.call(self._create_arguments(),
                        stdout=self.stdout,
                        stderr=self.stderr)

    def _open_subprocess(self):
        # Force bufsize=0 on all Python versions to avoid writing the
        # unflushed buffer when closing a broken input pipe
        self.player = subprocess.Popen(self._create_arguments(),
                                       stdin=self.stdin, bufsize=0,
                                       stdout=self.stdout,
                                       stderr=self.stderr)

        # Wait 0.5 seconds to see if program exited prematurely
        if not self.running:
            raise OSError("Process exited prematurely")

        if self.namedpipe:
            self.namedpipe.open("wb")
        elif self.http:
            self.http.open()

    def _close(self):
        # Close input to the player first to signal the end of the
        # stream and allow the player to terminate of its own accord
        if self.namedpipe:
            self.namedpipe.close()
        elif self.http:
            self.http.close()
        elif not self.filename:
            self.player.stdin.close()

        if self.kill:
            with ignored(Exception):
                self.player.kill()
        self.player.wait()

    def _write(self, data):
        if self.namedpipe:
            self.namedpipe.write(data)
        elif self.http:
            self.http.write(data)
        else:
            self.player.stdin.write(data)

class MulticastOutput(Output):
    """Output to (Multicast) UDP TS stream
    
    This isn't likely to be widely useful, so I'm not actually 
    making the code particularly reusable or self-contained.
    I'm using the multicast setup code from:
    
        github.com/mcfletch/pyzeroconf
        
    so *iff* you use this output then you wind up using LGPL 
    code in your app. (The code is only loaded if you actually 
    invoke the multicast option).
    """
    socket = None
    remainder = b''
    def __init__(self, url, filename=None, fd=None):
        self.url = url 
        parsed = self.parsed_url
        self.address = (parsed.hostname, int(parsed.port or 8000))
        self.interface_ip = parsed.fragment
    @property
    def parsed_url(self):
        import urlparse
        return urlparse.urlparse(self.url)
    def _open(self):
        from zeroconf import mcastsocket
        self.socket = mcastsocket.create_socket(
            (self.interface_ip or self.address[0], self.address[1]), 
            loop=True,
        )
        mcastsocket.join_group(self.socket, self.address[0], iface=self.interface_ip)
    def _close(self):
        if self.socket:
            # Should be critical section..
            socket = self.socket
            self.socket = None
            from zeroconf import mcastsocket
            try:
                mcastsocket.leave_group(
                    socket, self.address[0], iface=self.interface_ip
                )
            except Exception:
                pass 
            socket.close()
    def iterdata(self, data, size=188):
        # MPEG TS packets of 188 bytes
        offset = len(self.remainder)
        if offset:
            # we can *still* wind up with a 
            # short packet here
            yield self.remainder + data[:size-offset]
            self.remainder = b''
            offset = size-offset
        for i in range(offset, len(data)+1, size):
            packet = data[i:i+size]
            if len(packet) != size:
                self.remainder = packet 
                break
            else:
                yield packet
    def _write(self, data):
        socket = self.socket
        address = self.address
        for packet in self.iterdata(data):
            socket.sendto(packet, 0, address)
        

__all__ = ["PlayerOutput", "FileOutput", "MulticastOutput"]
