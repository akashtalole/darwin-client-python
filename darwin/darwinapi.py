#!/usr/bin/env python

"""darwin.py: Provide an API which allows the user to call Darwin filters from Python code"""

__author__ = "gcatto"
__version__ = "1.2"
__date__ = "22/01/19"
__license__ = "GPLv3"
__copyright__ = "Copyright (c) 2018 Advens. All rights reserved."


# "pip" imports
import ctypes
import json
import socket
import time

# local imports
from .darwinprotocol import DarwinPacket
from .darwinexceptions import DarwinInvalidArgumentError, DarwinConnectionError, DarwinTimeoutError


class DarwinApi:
    """
    A class used to call Darwin via a Unix socket or a TCP connection handled by HAProxy.

    Attributes
    ----------
    FILTER_CODE_MAP : dict
        a dict containing the different filter codes used by Darwin. This dict takes a plugin name as a key, and
        returns the associated filter code

    DEFAULT_TIMEOUT : float/None
        the default timeout (expressed in seconds). If None is set, no timeout is active

    socket : socket.socket
        the socket instance used to call Darwin

    verbose : bool
        whether to print debug lines or not

    Methods
    -------
    __init__(self, socket_type, **kwargs)
        Create a darwin.DarwinApi instance, either with a Unix or a TCP socket

    get_filter_code(filter_code)
        Return a filter code from a given filter name. This is case insensitive

    low_level_call(self, **kwargs)
        Perform an API call to Darwin, and return the results

    call(self,
         arguments,
         packet_type="other",
         response_type="no",
         filter_code=DarwinPacket.DARWIN_FILTER_CODE_NO,
         **kwargs)
        Perform an API call to Darwin, and return the result. This function is useful to make higher-level API calls to
        Darwin, compared to the darwin.DarwinApi.low_level_call method

    bulk_call(self,
              data,
              packet_type="other",
              response_type="no",
              filter_code=DarwinPacket.DARWIN_FILTER_CODE_NO,
              **kwargs)
        Perform a bulk API call to Darwin, and return the results

    close(self)
        Close the Darwin socket
    """

    FILTER_CODE_MAP = {
        "connection": 0x636E7370,
        "dga": 0x64676164,
        "hostlookup": 0x66726570,
        "injection": 0x696E6A65,
        "no": 0x00000000,
        "reputation": 0x72657075,
        "session": 0x73657373,
        "useragent": 0x75736572,
    }

    DEFAULT_TIMEOUT = 10

    @classmethod
    def get_filter_code(cls, filter_name):
        """
        Parameters
        ----------
        filter_name : str
            the name of the filter code (case insensitive)

        Returns
        -------
        int
            the associated filter code
        """

        return cls.FILTER_CODE_MAP[filter_name.lower()]

    def __init__(self, **kwargs):
        """
        Parameters
        ----------
        kwargs :
            verbose : bool
                whether to print debug info or not. Default is False

            socket_type : str
                the socket type to be used. "tcp" or "unix" (case insensitive)

            socket_path : str
                if the socket type given is "unix", this is the socket path which will be used to connect to Darwin

            socket_host : str
                if the socket type given is "tcp", this is the socket host which will be used to connect to Darwin

            socket_port : int
                if the socket type given is "tcp", this is the socket port which will be used to connect to Darwin

            timeout : float/None
                the timeout (expressed in seconds). If not given, the default timeout is set
        """

        self.verbose = kwargs.get("verbose", False)

        socket_type = kwargs.get("socket_type", None)

        if not socket_type or (socket_type.lower() != "tcp" and socket_type.lower() != "unix"):
            raise DarwinInvalidArgumentError("DarwinApi:: __init__:: You must give a socket type (tcp/unix)")

        try:
            darwin_timeout = kwargs["timeout"]

        except KeyError:
            darwin_timeout = self.DEFAULT_TIMEOUT

        self.socket = None

        if socket_type == "unix":
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            darwin_socket_path = kwargs.get("socket_path", None)

            if darwin_socket_path is None:
                raise DarwinInvalidArgumentError("DarwinApi:: __init__:: No socket path has been given")

            self.socket.setblocking(False)
            self.socket.settimeout(darwin_timeout)

            if self.verbose:
                print("DarwinApi:: __init__:: Connecting to " + str(darwin_socket_path) + "...")

            try:
                self.socket.connect(darwin_socket_path)
            except ConnectionError as error:
                raise DarwinConnectionError(str(error))

        else:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            darwin_socket_host = kwargs.get("socket_host", None)
            darwin_socket_port = kwargs.get("socket_port", None)

            if darwin_socket_host is None:
                raise DarwinInvalidArgumentError("DarwinApi:: __init__:: No socket host has been given")

            if darwin_socket_port is None:
                raise DarwinInvalidArgumentError("DarwinApi:: __init__:: No socket port has been given")

            self.socket.setblocking(False)
            self.socket.settimeout(darwin_timeout)

            if self.verbose:
                print("DarwinApi:: __init__:: Connecting to {darwin_socket_host}: {darwin_socket_port}...".format(
                    darwin_socket_host=darwin_socket_host,
                    darwin_socket_port=darwin_socket_port,
                ))

            try:
                self.socket.connect((darwin_socket_host, darwin_socket_port))
            except ConnectionError as error:
                raise DarwinConnectionError(str(error))

    def low_level_call(self, **kwargs):
        """
        Parameters
        ----------
        kwargs :
            socket_type : str
                the socket type to be used. "tcp" or "unix"

            header : darwin.DarwinPacket
                if provided, the darwin.DarwinPacket header instance to be sent to Darwin. If no header is given, a
                header description has to be provided (see the header_descr keyword argument)

            header_descr : dict
                if provided, the Darwin header description to create a darwin.DarwinPacket header instance, which will
                be sent to Darwin. Please refer to the darwin.DarwinPacket class documentation to know more about
                Darwin packets creation

            data : list
                the arguments to send to Darwin

        Returns
        -------
        dict
            the Darwin results. Currently, the dictionary only contains them, stored in a "certitude_list" key
        """

        if self.verbose:
            print("DarwinApi:: low_level_call:: Sending message to Darwin...")

        darwin_header = kwargs.get("header", None)
        darwin_data = kwargs.get("data", None)
        darwin_body = json.dumps(darwin_data)
        darwin_certitude = -1

        if darwin_header is None:
            darwin_header_descr = kwargs.get("header_descr", None)

            if darwin_header_descr is None:
                raise DarwinInvalidArgumentError("DarwinApi:: low_level_call:: No header nor description header given")

            darwin_header = DarwinPacket(**darwin_header_descr, verbose=self.verbose)

        try:
            darwin_packet_len = ctypes.sizeof(DarwinPacket) + ctypes.sizeof(ctypes.c_uint) * (len(darwin_data) - 1)
            if self.verbose:
                print("DarwinApi:: low_level_call:: Size of a Darwin packet: {darwin_packet_len} byte(s)".format(
                    darwin_packet_len=darwin_packet_len,
                ))

            if darwin_body is not None:
                darwin_header.body_size = len(darwin_body)

            else:
                darwin_header.body_size = 0

            if self.verbose:
                print("DarwinApi:: low_level_call:: Body size in the Darwin header set to {body_size}".format(
                      body_size=darwin_header.body_size,
                ))

                print("DarwinApi:: low_level_call:: Sending header to Darwin...")
                print("DarwinApi:: low_level_call:: Header description: " + str(darwin_header.get_python_descr()))

            self.socket.sendall(darwin_header)

            if darwin_body is not None:
                if self.verbose:
                    print("DarwinApi:: low_level_call:: Sending body \"{darwin_body}\" to Darwin...".format(
                        darwin_body=darwin_body,
                    ))

                self.socket.sendall(darwin_body.encode("utf-8"))

            else:
                if self.verbose:
                    print("DarwinApi:: low_level_call:: No body provided")

            if darwin_header.response_type == DarwinPacket.RESPONSE_TYPE["back"] or \
               darwin_header.response_type == DarwinPacket.RESPONSE_TYPE["both"]:
                if self.verbose:
                    print("DarwinApi:: low_level_call:: Receiving response from Darwin...")


                try:
                    bytes_received = 0
                    raw_response = b''

                    timeout = self.socket.gettimeout()

                    if timeout is not None:
                        past = time.time()

                    while bytes_received < darwin_packet_len:
                        if timeout is not None and time.time() - past > timeout:
                            raise socket.timeout

                        raw_response += self.socket.recv(darwin_packet_len)
                        bytes_received += len(raw_response)

                except socket.timeout as error:
                    raise DarwinTimeoutError(str(error))

                if self.verbose:
                    print("DarwinApi:: low_level_call:: Received {bytes_received} bytes".format(
                        bytes_received=bytes_received,
                    ))

                response = DarwinPacket(bytes_descr=raw_response, verbose=self.verbose)

                certitude_list = response.get_python_descr()["certitude_list"]

                if self.verbose:
                    print("DarwinApi:: low_level_call:: Certitude list obtained: {certitude_list}".format(
                        certitude_list=certitude_list,
                    ))

                return {
                    "certitude_list": certitude_list
                }

        except Exception as error:
            print("DarwinApi:: low_level_call:: Something wrong happened while calling the Darwin filter")
            raise error

    def call(self,
             arguments,
             packet_type="other",
             response_type="no",
             filter_code=DarwinPacket.DARWIN_FILTER_CODE_NO,
             **kwargs):
        """
        Parameters
        ----------
        arguments : list
            the list of arguments to be sent to Darwin. Please note that the arguments will be casted to strings

        packet_type : str
            the packet type to be sent. "darwin" for any packet coming from a Darwin filter, "other" for everything
            else

        response_type : str
            the response type which tells Darwin what it is expected to do. "no" to not answer anything, "back" to
            answer back to us, "darwin" to send the answer to the next filter, and "both" to apply both the "back" and
            "darwin" response types

        filter_code : int/str
            the filter code to be provided. If a string is given, darwin.DarwinApi will try to retrieve the filter code
            associated to it

        kwargs :
            other keyword arguments can be given. This function uses darwin.DarwinApi.low_level_call" internally. For a
            more advanced use, please refer to the darwin.DarwinApi.low_level_call method documentation

        Returns
        -------
        int
            the Darwin result
        """
        results = self.bulk_call([arguments],
                                 packet_type=packet_type,
                                 response_type=response_type,
                                 filter_code=filter_code,
                                 **kwargs)

        return result["certitude_list"][0]

    def close(self):
        """
        """

        print("DarwinApi:: low_level_call:: Closing socket")
        self.socket.close()

    def bulk_call(self,
                  data,
                  packet_type="other",
                  response_type="no",
                  filter_code=DarwinPacket.DARWIN_FILTER_CODE_NO,
                  **kwargs):
        """
        Parameters
        ----------
        data : list
            list of arguments to send to Darwin

        packet_type : str
            the packet type to be sent. "darwin" for any packet coming from a Darwin filter, "other" for everything
            else

        response_type : str
            the response type which tells Darwin what it is expected to do. "no" to not answer anything, "back" to
            answer back to us, "darwin" to send the answer to the next filter, and "both" to apply both the "back" and
            "darwin" response types

        filter_code : int/str
            the filter code to be provided. If a string is given, darwin.DarwinApi will try to retrieve the filter code
            associated to it

        kwargs :
            other keyword arguments can be given. This function uses darwin.DarwinApi.low_level_call" internally. For a
            more advanced use, please refer to the darwin.DarwinApi.low_level_call method documentation

        Returns
        -------
        list
            the Darwin results stored in a list
        """
        if isinstance(filter_code, str):
            try:
                filter_code = self.get_filter_code(filter_code)

            except KeyError:
                raise DarwinInvalidArgumentError("DarwinApi:: call:: The filter code provided "
                                                 "(\"{filter_code}\") does not exist. "
                                                 "Accepted values are: {accepted_values}".format(
                                                      filter_code=filter_code,
                                                      accepted_values=", ".join(self.FILTER_CODE_MAP.keys()),
                                                  ))

        return self.low_level_call(header_descr={
                                       "packet_type": packet_type,
                                       "response_type": response_type,
                                       "filter_code": filter_code,
                                   },
                                   data=data,
                                   **kwargs)


if __name__ == "__main__":
    print("__main__:: {file_name} has been called directly. Demo:".format(file_name=__file__), )

    print("\n***\n")
    # parameters needed for the demo
    # body: what the Darwin filter will take as an input

    body = "GET /helloworld.html HTTP/1.1 accept: application/xml authorization: "\
           "Basic WXVxb3N1YXI6dTR4UEI4NDc2Mzll referer: https://mysuperwebsite.com/helloworld.html "\
           "host: livemysuperwebsite.mysuperwebsite.org"

    # socket_path: the Darwin filter socket path
    socket_path = "/var/sockets/darwin/injection_1.sock"
    # response_type:
    # > "no": no response from Darwin
    # > "back": Darwin responds directly to the caller
    # > "darwin": Darwin sends the response to the next filter
    # > "both": Darwin responds back to the caller, and sends the response to the next filter
    response_type = "back"
    # filter_code: the unique ID of the Darwin filter
    filter_code = DarwinApi.get_filter_code("injection")

    print("__main__:: Asking the injection filter whether this request is malicious or not:")
    print(body)

    print("\n***\n")
    print("__main__:: Calling DarwinApi...")

    darwin_api = DarwinApi(socket_path=socket_path,
                           socket_type="unix", )

    # you call also call the Darwin API with the "raw call" function (low_level_call), which is called by call:
    # darwin_response = darwin_api.low_level_call(header_descr={
    #                                                 "response_type": response_type,
    #                                                 "filter_code": filter_code,
    #                                             },
    #                                             body=body, )

    darwin_api.close()

    print("\n***\n")
    print("__main__:: Response received from the Darwin filter:")
    print("__main__:: {darwin_response}".format(darwin_response=darwin_response, ))
    print("\n***\n")
    print("__main__:: End of demo!")