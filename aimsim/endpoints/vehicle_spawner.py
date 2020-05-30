"""
A vehicle spawner behaves like the latter half of a road, in that it creates a
vehicle and uses the lane `LeavingInterface` to send the vehicle onto a lane
in the network.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Optional, List, Any, Type, Iterable
from random import random, choices, sample

import aimsim.shared as SHARED
from ..archetypes import Configurable, Upstream
from ..util import VehicleTransfer, LinkError, Coord, VehicleSection
from ..vehicles import Vehicle
from ..roads import Road
from .generators import Generator, NormalGenerator


class VehicleSpawner(Configurable, Upstream):

    def __init__(self,
                 downstream: Road,
                 vpm: float,  # vehicles per minute
                 generator_ps: List[float],
                 generator_type: List[Type[Generator]],
                 generator_specs: List[Dict[str, Any]]
                 ) -> None:
        """Create a new vehicle spawner.

        Keyword arguments:
        rates -- Gives a Poisson arrival rate for each vehicle class
        """

        if len(generator_type) != len(generator_specs) != len(generator_ps):
            raise ValueError("The number of generator types and specs must "
                             "match.")
        if sum(generator_ps) != 1:
            raise ValueError("The generator probabilities must sum to 1.")

        self.downstream = downstream

        # Given vehicles per minute and a Poisson process, calculate the
        # probability of spawning a vehicle in each timestep.
        self.p = (vpm/60) * SHARED.TIMESTEP_LENGTH  # veh/min * min/sec * s
        # Specifically, this is the probability of spawning at least one
        # vehicle in each timestep, but we assume that the probability of
        # spawning more than one vehicle is so low and difficulty of spawning
        # multiple vehicles at once that we combine the small probabilities for
        # spawns > 1 together with the probability of spawns == 1.

        # Record generator use probabilities and create the vehicle generators
        # from the given specifications.
        self.generator_ps: List[float] = generator_ps
        self.generators: List[Generator] = []
        for i in range(len(generator_type)):
            self.generators.append(
                generator_type[i].from_spec(generator_specs[i])
            )

        # Prepare a queued spawn to fill later.
        self.queued_spawn: Optional[Vehicle] = None

        raise NotImplementedError("TODO")

    @staticmethod
    def spec_from_str(spec_str: str) -> Dict[str, Any]:
        """Reads a spec string into a spawner spec dict."""

        spec: Dict[str, Any] = {}

        # TODO: interpret the string into the spec dict
        raise NotImplementedError("TODO")

        # TODO: enforce provision of separate lists of generator_type and
        #       generator_config in spawner spec string
        generator_type_strs: List[str]
        generator_spec_strs: List[str]
        if len(generator_type_strs) != len(generator_spec_strs):
            raise ValueError("Number of Generator types and specs don't "
                             "match.")
        # Based on the spec, identify the correct generator types
        spec['generator_type'] = []
        spec['generator_specs'] = []
        for i in range(generator_type_strs):
            generator: Generator
            generator_spec: Dict[str, Any]
            gen_str: str = generator_type_strs[i]
            gen_spec_str: str = generator_spec_strs[i]
            if gen_str.lower() in {'normal', 'normalgenerator'}:
                generator = NormalGenerator
                generator_spec = NormalGenerator.str_from_spec(gen_spec_str)
            else:
                raise ValueError("Unsupported Generator type.")
            spec['generator_type'].append(NormalGenerator)
            spec['generator_specs'].append()

        return spec

    @classmethod
    def from_spec(cls, spec: Dict[str, Any]) -> VehicleSpawner:
        """Create a new VehicleSpawner from the given spec.

        The output of spec_from_str needs to get the actual downstream Road
        after it's created.
        """
        return cls(
            downstream=spec['downstream'],
            vpm=spec['vpm'],
            generator_ps=spec['generator_ps'],
            generator_type=spec['generator_types'],
            generator_specs=spec['generator_specs']
        )

    def step_vehicles(self) -> Optional[Vehicle]:
        """Decides if to spawn a vehicle. If so, processes and returns it."""

        if self.downstream is None:
            raise LinkError("No downstream object.")
        elif self.downstream is not Road:
            raise LinkError("Downstream object is not RoadLane.")

        spawn: Vehicle
        if self.queued_spawn is not None:
            # There's a queued spawn that didn't make it last timestep.
            # Take it out of the queue and try spawning it again.
            spawn = self.queued_spawn
            self.queued_spawn = None
        else:
            # Roll to spawn a new vehicle.
            if random() < self.p:
                # Pick a generator to use based on the distribution of
                # generators and use it to spawn a vehicle.
                spawn = choices(self.generators,
                                self.generator_ps)[0].create_vehicle()
            else:
                # No vehicle spawned. Return nothing.
                return None

        # 1. Determine which lane to spawn to spawn the vehicle in.
        #    Shuffle the lanes in the downstream road and iterate through for
        #    the first lane that works for the spawned vehicle's next movement.
        #    If we find that no lanes work, ever, error.
        can_work: bool = False
        for lane in sample(self.downstream.lanes,
                           k=len(self.downstream.lanes)):
            if len(spawn.next_movement(lane.end_coord)) > 0:
                can_work = True
                # Check if the lane it's trying to spawn into has enough space.
                if lane.free_space() > (spawn.length
                                        * SHARED.length_buffer_factor):
                    # If so, place it in the downstream buffer and return it.
                    self.downstream.transfer_vehicle(VehicleTransfer(
                        vehicle=spawn,
                        section=VehicleSection.FRONT,
                        t_left=None,
                        pos=lane.end_coord
                    ))
                    self.downstream.transfer_vehicle(VehicleTransfer(
                        vehicle=spawn,
                        section=VehicleSection.CENTER,
                        t_left=None,
                        pos=lane.end_coord
                    ))
                    self.downstream.transfer_vehicle(VehicleTransfer(
                        vehicle=spawn,
                        section=VehicleSection.REAR,
                        t_left=None,
                        pos=lane.end_coord
                    ))
                    return spawn
        if not can_work:
            # None of the lanes work for the vehicle's desired movement.
            raise RuntimeError("Spawned vehicle has no eligible lanes.")
        else:
            # At least one of the lanes could spawn this vehicle, but none of
            # them had enough room for it. Queue it for the next timestep.
            self.queued_spawn = spawn
            return None
