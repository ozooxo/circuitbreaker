# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

from functools import wraps
from datetime import datetime, timedelta
from inspect import isgeneratorfunction
from typing import AnyStr, Iterable
from math import ceil, floor

try:
    from time import monotonic
except ImportError:
    from monotonic import monotonic

STATE_CLOSED = 'closed'
STATE_OPEN = 'open'
STATE_HALF_OPEN = 'half_open'


class CircuitBreaker(object):
    FAILURE_THRESHOLD = 5
    RECOVERY_TIMEOUT = 30
    EXPECTED_EXC = Exception
    FALLBACK_FUNCTION = None

    def __init__(self,
                 failure_threshold=None,
                 recovery_timeout=None,
                 expected_exception=None,
                 name=None,
                 fallback_function=None):
        self._last_failure = None
        self._failure_count = 0
        self._failure_threshold = failure_threshold or self.FAILURE_THRESHOLD
        self._recovery_timeout = recovery_timeout or self.RECOVERY_TIMEOUT
        self._expected_exception = expected_exception or self.EXPECTED_EXC
        self._fallback_function = fallback_function or self.FALLBACK_FUNCTION
        self._name = name
        self._state = STATE_CLOSED
        self._opened = monotonic()

    def __call__(self, wrapped):
        return self.decorate(wrapped)

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc_value, _traceback):
        if exc_type and issubclass(exc_type, self._expected_exception):
            # exception was raised and is our concern
            self._last_failure = exc_value
            self.__call_failed()
        else:
            self.__call_succeeded()
        return False  # return False to raise exception if any

    def decorate(self, function):
        """
        Applies the circuit breaker to a function
        """
        if self._name is None:
            self._name = function.__name__

        CircuitBreakerMonitor.register(self)

        if isgeneratorfunction(function):
            call = self.call_generator
        else:
            call = self.call

        @wraps(function)
        def wrapper(*args, **kwargs):
            if self.opened:
                if self.fallback_function:
                    return self.fallback_function(*args, **kwargs)
                raise CircuitBreakerError(self)
            return call(function, *args, **kwargs)

        return wrapper

    def call(self, func, *args, **kwargs):
        """
        Calls the decorated function and applies the circuit breaker
        rules on success or failure
        :param func: Decorated function
        """
        with self:
            return func(*args, **kwargs)

    def call_generator(self, func, *args, **kwargs):
        """
        Calls the decorated generator function and applies the circuit breaker
        rules on success or failure
        :param func: Decorated generator function
        """
        with self:
            for el in func(*args, **kwargs):
                yield el

    def __call_succeeded(self):
        """
        Close circuit after successful execution and reset failure count
        """
        self._state = STATE_CLOSED
        self._last_failure = None
        self._failure_count = 0

    def __call_failed(self):
        """
        Count failure and open circuit, if threshold has been reached
        """
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._state = STATE_OPEN
            self._opened = monotonic()

    @property
    def state(self):
        if self._state == STATE_OPEN and self.open_remaining <= 0:
            return STATE_HALF_OPEN
        return self._state

    @property
    def open_until(self):
        """
        The approximate datetime when the circuit breaker will try to recover
        :return: datetime
        """
        return datetime.utcnow() + timedelta(seconds=self.open_remaining)

    @property
    def open_remaining(self):
        """
        Number of seconds remaining, the circuit breaker stays in OPEN state
        :return: int
        """
        remain = (self._opened + self._recovery_timeout) - monotonic()
        return ceil(remain) if remain > 0 else floor(remain)

    @property
    def failure_count(self):
        return self._failure_count

    @property
    def closed(self):
        return self.state == STATE_CLOSED

    @property
    def opened(self):
        return self.state == STATE_OPEN

    @property
    def name(self):
        return self._name

    @property
    def last_failure(self):
        return self._last_failure

    @property
    def fallback_function(self):
        return self._fallback_function

    def __str__(self, *args, **kwargs):
        return self._name


class CircuitBreakerError(Exception):
    def __init__(self, circuit_breaker, *args, **kwargs):
        """
        :param circuit_breaker:
        :param args:
        :param kwargs:
        :return:
        """
        super(CircuitBreakerError, self).__init__(*args, **kwargs)
        self._circuit_breaker = circuit_breaker

    def __str__(self, *args, **kwargs):
        return 'Circuit "%s" OPEN until %s (%d failures, ' \
               '%d sec remaining) (last_failure: %r)' % (
                   self._circuit_breaker.name,
                   self._circuit_breaker.open_until,
                   self._circuit_breaker.failure_count,
                   round(self._circuit_breaker.open_remaining),
                   self._circuit_breaker.last_failure,
               )


class CircuitBreakerMonitor(object):
    circuit_breakers = {}

    @classmethod
    def register(cls, circuit_breaker):
        cls.circuit_breakers[circuit_breaker.name] = circuit_breaker

    @classmethod
    def all_closed(cls):
        # type: () -> bool
        return len(list(cls.get_open())) == 0

    @classmethod
    def get_circuits(cls):
        # type: () -> Iterable[CircuitBreaker]
        return cls.circuit_breakers.values()

    @classmethod
    def get(cls, name):
        # type: (AnyStr) -> CircuitBreaker
        return cls.circuit_breakers.get(name)

    @classmethod
    def get_open(cls):
        # type: () -> Iterable[CircuitBreaker]
        for circuit in cls.get_circuits():
            if circuit.opened:
                yield circuit

    @classmethod
    def get_closed(cls):
        # type: () -> Iterable[CircuitBreaker]
        for circuit in cls.get_circuits():
            if circuit.closed:
                yield circuit


def circuit(failure_threshold=None,
            recovery_timeout=None,
            expected_exception=None,
            name=None,
            fallback_function=None,
            cls=CircuitBreaker):
    # if the decorator is used without parameters, the
    # wrapped function is provided as first argument
    if callable(failure_threshold):
        return cls().decorate(failure_threshold)
    else:
        return cls(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception,
            name=name,
            fallback_function=fallback_function)
