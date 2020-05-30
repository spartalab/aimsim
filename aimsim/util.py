"""
Contains utility functions that makes other stuff work.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import (TYPE_CHECKING, TypeVar, Type, NamedTuple, Optional, Any,
                    Iterable)

if TYPE_CHECKING:
    from .vehicles import Vehicle


class Coord(NamedTuple):
    x: float
    y: float


class VehicleSection(Enum):
    FRONT = 0
    CENTER = 1
    REAR = 2


class SpeedUpdate(NamedTuple):
    v: float
    a: float


class VehicleTransfer(NamedTuple):
    """Pattern for transferring a vehicle from one object to another.

    This is primarily used to keep step_vehicles position updates consistent.
    Data necessary for speed updates should be handled by reservation requests.

    Parameters:
        vehicle: Vehicle
            The vehicle to be added.
        vehicle: VehicleSection
            The FRONT, CENTER, or REAR of the vehicle being transferred.
        t_left: Optional[float]
            The vehicle moved some distance along the lane it came from in
            the current timestep before it reached the transition to this
            lane. This is the time left in the timestep after that move.
            If t_left is only None in the case where the vehicle enters from a
            spawner onto the intersection, in which case the Road needs to
            initialize its position.
        pos: Coord
            The position of the end of the lane the vehicle is exiting from.
    """
    vehicle: Vehicle
    section: VehicleSection
    t_left: Optional[float]
    pos: Coord


class CollisionError(Exception):
    """Raised when vehicles collide."""
    pass


class LinkError(Exception):
    """Raised when a road doesn't find an upstream or downstream object."""
    pass


class TooManyProgressionsError(Exception):
    def __init__(self,
                 msg='More than one vehicle transitioned between lanes in the '
                     'same timestep. This is usually result of the '
                     'ticks_per_second config being too low. Try increasing '
                     'it and running again.',
                 *args, **kwargs):
        super().__init__(msg, *args, **kwargs)
