"""
The `lanes` module handles the progression of vehicles along 1-dimensional
lines we call "lanes", used on roads and within intersections (e.g., as a left
turn trajectory), making them the fundamental building block of aimsim.

Lanes only interact with their Road and Intersection containers, and so must be
written to interact through their parent road or intersection to pass vehicles
between lanes, either as a lane change or onto the next road of intersection.

The geometry of a lane is determined by what type of Trajectory is
provided when initialized. The Trajectory controls the physics of vehicles on
the lane when they move forward, while the Lane is responsible for holding
proportional progress of the vehicles.
"""

import heapq
from abc import ABC, abstractmethod
from typing import Tuple, Iterable, Optional, List, NamedTuple, Dict

import aimsim.shared as SHARED
from .archetypes import Upstream
from .util import (Coord, CollisionError, VehicleSection, SpeedUpdate,
                   TooManyProgressionsError, VehicleTransfer)
from .intersections.reservations import ReservationRequest
from .vehicles import Vehicle
from .trajectories import Trajectory


class Lane(ABC):

    class VehicleProgress(NamedTuple):
        front: float
        center: float
        rear: float

    def __init__(self,
                 trajectory: Trajectory,
                 offset: Coord = Coord(0, 0),
                 speed_limit: int = SHARED.speed_limit) -> None:
        """Create a new lane.

        Lanes inherit the trajetory of the road they're created by (if
        applicable), adding an offset if necessary.

        Keyword arguments:
        trajectory: The trajectory of the road that contains the lane.
        offset: The amount by which to offset the lane by.
        """
        # initialize lane
        self.vehicles: List[Vehicle] = []
        self.vehicle_progress: Dict[Vehicle, Lane.VehicleProgress] = {}
        self.trajectory = trajectory
        self.speed_limit = speed_limit
        self.request_has_changed = False

        # TODO: intersection lanes are not allowed be be offset, maybe update
        #       the generic lane to reflect that?
        self.start_coord: Coord = Coord(trajectory.start_coord.x + offset.x,
                                        trajectory.start_coord.y + offset.y)
        self.end_coord: Coord = Coord(trajectory.end_coord.x + offset.x,
                                      trajectory.end_coord.y + offset.y)

        raise NotImplementedError("TODO")

    # Support functions for speed updates

    def effective_speed_limit(self, p: float) -> float:
        """Return the effective speed limit at the given progression.

        Some trajectories may require a lower speed limit, e.g. on sharper
        turns. Overriden in IntersectionLane to prevent crashes just downstream
        when control is done by signals instead of by reservation.
        """
        return min(self.speed_limit, self.trajectory.effective_speed_limit(p))

    def update_speeds_by_section(self,
                                 p_max: float = 1,
                                 p_min: float = -1,
                                 ) -> Dict[Vehicle, SpeedUpdate]:
        """Return speed updates for vehicles in (p_min, p_max]

        Parameters
            p_min: float = -1
                Minimum proportion is 0 but we want to account for boundary
                conditions since transferring vehicles will be placed at 0.
            p_max: float
            check_preceding: bool
                Whether to compare speeds for a vehicle against the preceding
                vehicle. Not necessary for vehicles in the intersection with a
                reservation, as they've already secured a reservation that
                requires them increase speed as much as possible.
        """
        if p_max < p_min:
            raise ValueError("p_max must be greater than or equal to p_min")

        new_speed: Dict[Vehicle, SpeedUpdate] = {}

        preceding: Optional[Vehicle] = None
        # self.vehicles should be in order of decreasing progress
        for vehicle in self.vehicles:
            if self.vehicle_progress[vehicle].front <= p_min:
                # exit loop if we reach a vehicle with progress < p_min
                break

            if self.vehicle_progress[vehicle].front <= p_max:
                # don't update speeds of vehicles with progress > p_max
                a_new = self.accel_update(vehicle, preceding)
                new_speed[vehicle] = self.speed_update(vehicle, a_new)

            preceding = vehicle

        return new_speed

    @abstractmethod
    def accel_update(self, vehicle: Vehicle,
                     preceding: Optional[Vehicle]) -> float:
        """Return a vehicle's acceleration update.

        Note that this does NOT change the vehicle's position.

        `preceding` is the vehicle ahead of the vehicle we're calculating for.
        If there is none, the vehicle is at the head of the queue and must stop
        at the intersection line if it doesn't have a reservation.
        """
        raise NotImplementedError("Must be implemented in child classes.")

    def accel_update_uncontested(self, vehicle: Vehicle) -> float:
        """Return accel update if there are no vehicles ahead."""
        effective_speed_limit = self.effective_speed_limit(
            self.vehicle_progress[vehicle].front)
        if vehicle.v > effective_speed_limit:
            return vehicle.max_braking
        elif vehicle.v == effective_speed_limit:
            return 0
        else:  # vehicle.v < effective_speed_limit
            return vehicle.max_accel

    def accel_update_following(self,
                               vehicle: Vehicle,
                               pre_p: float = 1,
                               pre_v: float = 0,
                               pre_a: float = 0) -> float:
        """Return speed update to prevent collision with a preceding object.

        The preceding object is either a preceding vehicle with proportional
        progress, velocity, acceleration broken out or, given the defaults, the
        intersection line.
        """
        effective_speed_limit = self.effective_speed_limit(
            self.vehicle_progress[vehicle].front)

        # TODO: when implementing, take into account that time is discrete so
        #       round down when spacing.

        a_maybe = self.accel_update_uncontested(vehicle)
        if a_maybe < 0:  # need to brake regardless of closeness
            return a_maybe
        else:
            # TODO: calculate closeness
            raise NotImplementedError("TODO")
            # if (too close based on stopping distances):
            #     return vehicle.max_braking
            # else:
            #     # override a_maybe iff we can accel but that would put the
            #     # vehicles too close together
            #     return 0 if (can't get closer) else a_maybe

    def speed_update(self, vehicle: Vehicle, accel: float) -> SpeedUpdate:
        """Given an accelerationn, update speeds.

        Note acceleration and speed aren't 1-to-1 because of discrete time.
        """
        v_new = vehicle.v + accel*SHARED.TIMESTEP_LENGTH
        if v_new < 0:
            return SpeedUpdate(v=0, a=accel)
        else:
            effective_speed_limit = self.effective_speed_limit(
                self.vehicle_progress[vehicle].front)
            if v_new > effective_speed_limit:
                return SpeedUpdate(v=effective_speed_limit, a=accel)
            else:
                return SpeedUpdate(v=v_new, a=accel)

    # Support functions for vehicle transfers

    def enter_vehicle_section(self, vehicle_transfer: VehicleTransfer) -> None:
        """Adds a section of a transferring vehicle to the lane."""
        # TODO: add a vehicle to this lane and progress it forward using the
        #       time in the timestep it has left
        raise NotImplementedError("TODO")


class RoadLane(Lane):
    """RoadLanes connect intersections, providing them access and egress.

    Like their parent Roads, RoadLanes are divided into three sections: the
    entrance, lane changing, and approach regions.

    They connect directly to the conflict area controlled by `manager`s.
    """

    def __init__(self,
                 trajectory: Trajectory,
                 offset: Coord = Coord(0, 0),
                 len_entrance_region: float = SHARED.min_entrance_length,
                 len_approach_region: float = 100,
                 speed_limit: int = SHARED.speed_limit) -> None:
        super().__init__(trajectory=trajectory,
                         offset=offset,
                         speed_limit=speed_limit)

        # TODO: use len_entrance_region and len_approach_region to calculate
        #       the proportional cutoffs for the three regions, for use in the
        #       the speed update and step functions
        # Note that we start at the front of the lane and work back, so
        # proportions decrease as we go on.
        # self.approach_end: float = 1
        self.lcregion_end: float = 0.6
        self.entrance_end: float = 0.3

        raise NotImplementedError("TODO")

    # Support functions for speed updates

    def accel_update(self, vehicle: Vehicle,
                     preceding: Optional[Vehicle]) -> float:

        if preceding is None:
            # don't need to check against the preceding vehicle
            if vehicle.can_enter_intersection:
                # full speed forward
                # TODO: does this run into collision issues for signalized?
                return self.accel_update_uncontested(vehicle)
            else:
                # stop at the intersection line
                return self.accel_update_following(vehicle)
        else:
            a_follow = self.accel_update_following(vehicle,
                                                   pre_p=self.vehicle_progress[
                                                       preceding].rear,
                                                   pre_v=preceding.v,
                                                   pre_a=preceding.a)
            if (preceding.can_enter_intersection
                    and vehicle.can_enter_intersection):
                # follow preceding vehicle into the intersection
                return a_follow
            else:
                # stop for preceding vehicle AND intersection line
                return min(a_follow, self.accel_update_following(vehicle))

    def update_speeds(self, section: int) -> Dict[Vehicle, SpeedUpdate]:
        """Return speed updates for all vehicles on this road lane."""
        # Error check
        if (section not in {1, 2, 3}):
            raise ValueError("section must be 1, 2, or 3")

        if section == 1:  # approach region
            return self.update_speeds_by_section(p_min=self.lcregion_end)
        elif section == 2:  # LCM region
            return self.update_speeds_by_section(p_max=self.lcregion_end,
                                                 p_min=self.lcregion_end)
        else:  # entrance region
            return self.update_speeds_by_section(p_max=self.lcregion_end)

    # Support functions for step updates

    def step_approach(self) -> Optional[VehicleTransfer]:
        """Step vehicles in the approach region forward."""

        # TODO: set flag for if the ReservationRequest has changed since the
        #       last setup. this only applies when we cache requests instead of
        #       responding in real time (e.g. for non-FCFS systems), but should
        #       be implemented for use in cached. the reservation changes iff a
        #       new vehicle arrives right behind the current group of vehicles
        #       that are waiting to turn with the same movement AND the manager
        #       has set a delay until the next accepted request (batching).

        # TODO: A sequence of consecutive vehicles behaves like this: all
        #       vehicles accelerate
        #       as much as possible (brake as slowly as possible), until one of
        #       them breaks the intersection line, at which point they slow
        #       down to the acceleration/braking rate of the worst vehicle, in
        #       order to maintain a constant length. The RoadLane needs to
        #       handle this change in acceleration by replacing the vehicles in
        #       a Sequence with the Sequence Vehicle-like in-place as soon as
        #       the first vehicle in the Sequence breaks the intersection line.

        # save a pointer to the preceding vehicle before operating on the next
        # one so you can make sure you don't crash with it in the worst case
        preceding: Optional[Vehicle] = None

        leaving: Optional[VehicleTransfer] = None

        for vehicle in self.vehicles:

            to_check: Iterable[VehicleSection] = list(VehicleSection)

            if self.vehicle_progress[VehicleSection.FRONT] is None:
                # This vehicle has entered the intersection and the
                # responsibility of updating its reference position
                # (vehicle.pos) now belongs to the intersection.

                # However, because roads and intersections can be updated
                # asynchronously, we need to infer its new position in the
                # context of this lane.

                # By our model, since the vehicle has entered the intersection,
                # it has a reservation and is accelerating as much as possible.

                # update the vehicle's properties (pos, speed, accel) according
                # to its kinematics as far as possible without hitting the back
                # of `preceding`.
                #
                # If a vehicle has a reservation, move it as if it doesn't have
                # to stop at the intersection line. otherwise, move it as if it
                # does have to stop.
                to_check = [VehicleSection.CENTER, VehicleSection.REAR]
            else:
                # Calculate the movement of the vehicle based on its front
                # position and propogate it to the vehicle's properties.
                pass

            for section in to_check:
                # update the lane's record of the vehicle's progress according
                # to its properties. (This allows for a vehicle to be
                # controlled by its front, if it's in a different lane, and
                # have its movement propogated back to its back sections)
                raise NotImplementedError("TODO")

            preceding = vehicle

            # break out of the loop once you exit the approach region
            # if something:
            # break

        return leaving

    def step_entrance(self) -> None:
        """Step vehicles in the entrance region forward."""
        preceding = None
        for vehicle in self.vehicles:
            raise NotImplementedError("TODO")
            preceding = vehicle

    # Used by intersection manager via road methods

    def check_entrance_collision(self, steps_forward: int,
                                 entering_vehicle: Vehicle,
                                 entering_v0: float,
                                 entering_accel_profile: Iterable[float]
                                 ) -> bool:
        """Returns true if proposed movement may cause a collision.

        We need to ensure that a vehicle that wants to enter one of this road's
        lanes won't collide with any vehicles currently in the lane. To do so
        we look ahead to when the new vehicle is proposing to enter the lane
        with a simple heuristic.

        Given what velocity and acceleration profile the new vehicle is
        proposing to enter the lane at, we isolate the position and speed of
        the last vehicle in the lane at the current timestep and assume it
        starts and continues braking between the current timestep and the
        target timestep(s). Given this behavior for the vehicle currently in
        the lane, we check if the entering vehicle will collide with the
        in-lane vehicle if it follows its proposed speed and acceleration
        profile. If so, return `True`. If the proposed vehicle won't collide,
        return `False`.

        This function should be called by `VehicleSpawner` to check if a
        vehicle will collide before spawning it, and by `IntersectionManager`
        to check if a reservation will collide as it exits the intersection.

        Parameters:
            steps_forward : int
                how many steps forward does the vehicle start entering
            entering_vehicle : Vehicle
                the vehicle entering
            entering_v0 : float
                at steps_forward, what is its speed
            entering_accel_profile : Iterable[float]
                what this vehicle's acceleration from the time it starts
                entering to the time it finishes entering the lane
        """

        # TODO: Consider caching the worst-case profile of the last vehicle
        #       at each step instead of calculating it fresh each time. Maybe
        #       just calculate the lane position at which the new vehicle would
        #       be considered "colliding" at each timestep as needed, caching
        #       them for future calls. Clear this cache on a new step() call.

        # TODO: I think I'm forgetting an input arg but I forget what.

        # TODO: Maybe add a check that puts the distance to collision at the
        #       min of (projected position of the last vehicle, edge of
        #       entrance region)

        raise NotImplementedError("TODO")

    def next_reservation_candidate(self,
                                   group: bool = False,
                                   all_groups: bool = False,
                                   delay: int = 0,
                                   value: bool = False
                                   ) -> Optional[Iterable[ReservationRequest]]:
        """Return the first vehicle/platoon without a reservation if it exists.

        Parameters:
            group: bool
                Whether to collect vehicles of the same class (human/auto/semi)
                and movement (Intersection end Coord) together into a platoon.
            all_groups: bool
                If grouping, whether to return every combination of groups
                possible. If not, just return the biggest group.
            delay: int
                Return a reservation given that it can't start until this many
                timesteps from now.
        """

        if all_groups and not group:
            raise ValueError("all_groups only valid if group is true.")
        if (delay < 0) or (type(delay) is not int):
            raise ValueError("delay must be non-negative.")

        # TODO: check if the vehicle in front of the current first vehicle
        #       without a reservation is a Platoon. if so, the preceding
        #       vehicle's acceleration capability may change once its front
        #       enters the intersection, altering the calcultion for the
        #       current vehicle's min_time_to_intersection

        # TODO: check a flag to see if it should bother returning a request or
        #       if it'll be the same as the last time this lane was polled

        # look through the approach region for the first Vehicle that wants an
        # intersection reservation and doesn't have one yet. (A human-driven
        # vehicle in an FCFS-Light setup wouldn't want a reservation.) If
        # group, return a sequence of vehicles that fit the criteria.

        # TODO: implement checking for the value flag to sum vot across the
        #       lane andreturn nonzero value the ReservationRequest(s)
        if not group:
            raise NotImplementedError("TODO")
        else:
            # calculate min time based on slowest vehicle in lane
            # make sure to look at every vehicle in the entrance region
            # following the lead vehicle that has the same movement, even if
            # they're not lined up neatly behind the first vehicle.

            if all_groups:
                # returning a bundle of candidate platoons that range from
                # including just the first vehicle to every vehicle so the
                # policy can better sequence auctions
                # TODO: maybe just reprocess the platoon packet in the policy
                #       instead of doing this complicated thing?
                raise NotImplementedError("TODO")

    def min_time_to_intersection(self, vehicle: Vehicle) -> int:
        """Return a vehicle's minimum time to intersection in timesteps."""
        # TODO: abstract away behavior in update_speeds to figure this out
        raise NotImplementedError


class IntersectionLane(Lane):
    """
    Like RoadLanes, but without the three-region specification and vehicles
    have more consistent in-lane behavior, at least when identified as
    deterministic. Unlike RoadLanes, IntersectionLanes can potentially be
    uncertain; we aren't sure if the vehicle using it is going to follow
    instructions exactly 100% of the time. Here, instead of being the ground
    truth, the trajectory is just a prior. In the update_*_speeds and step
    functions, vehicles' properties (pos/v/heading) are updated with some
    amount of randomness off of the centerline trajectory.
    """

    def __init__(self):
        super().__init__()
        self.reset_temp_speed_limit()

    # Support functions for speed updates

    def reset_temp_speed_limit(self):
        """Reset the soft speed limit to prevent crashes just downstream.

        In signalized intersections, we don't have full control over vehicles.
        Thus we need to ensure that there's enough room between vehicles just
        outside of the intersection to avoid collisions, even if we can no
        longer see the vehicle just downstrea. Solve this by caching the speed
        of recently exited vehicles and requiring that no vehicles on this
        trajectory the same cycle exceeds that.
        """
        self.temp_speed_limit = self.speed_limit
        self.last_exit: int = SHARED.t

    def effective_speed_limit(self, p: float) -> float:
        """Return the effective speed limit at the given progression.

        Some trajectories may require a lower speed limit, e.g. on sharper
        turns.
        """
        return min(self.temp_speed_limit, super().effective_speed_limit(p))

    def update_speeds(self) -> Dict[Vehicle, SpeedUpdate]:
        return self.update_speeds_by_section()

    def accel_update(self, vehicle: Vehicle,
                     preceding: Optional[Vehicle]) -> float:
        if vehicle.has_reservation or (preceding is None):
            # vehicle has a confirmed reservation that assumed maximum
            # acceleration, so we're free to behave as if it's uncontested
            # OR there is no vehicle ahead and the lane has traffic signal
            # permission, so the temporarily lower speed limit is in effect
            return self.accel_update_uncontested(vehicle)
        else:
            # lane has traffic signal permission and there's a vehicle ahead
            # to follow
            return self.accel_update_following(vehicle,
                                               pre_p=self.vehicle_progress[
                                                   preceding].rear,
                                               pre_v=preceding.v,
                                               pre_a=preceding.a)

    # Support functions for step updates

    def step(self) -> Optional[VehicleTransfer]:
        """

        Uses  equations defined in the spec to progress the completion of each
        vehicle. The general logic of the code is to iterate through all the
        vehicles and then progress each car. We then check to see if there is a
        car that can enter the intersecton and then pop it off from the queue
        and return it to the parent road or intersection so they can figure out
        what to do with it.

        Alternatively, for each vehicle at completion percentage, see if we can
        move it forward. If the head vehicle can get a reservation given an
        approximate arrival time then we should remove the vehicle from the
        dictionary

        Returns
            A VehicleTransfer object if a vehicle leaves the lane.
        """

        # TODO: implement stochasticity in IntersectionLane
        #       If vehicles deviate from centerline, it will be because their
        #       IntersectionLane told them to. IntersectionLane has flag for if
        #       a vehicle will stochastically deviate from acceleration
        #       instructions and by how much, but otherwise progression is like
        #       RoadLane, except IntersectionLane doesn't need to calculate how
        #       to avoid collisions since the reservations system takes care of
        #       that already (unless we have stochastic movement and get really
        #       bad RNG).

        # TODO: when the vehicle exits the intersection lane, remember to set
        #       its has_reservation property to false

        # TODO: when the lane is signalized and a vehicle exits (i.e., a
        #       vehicle without a reservation exits), call temporary speed
        #       limit

        # reset speed limit when 30s pass since the last exit.
        if SHARED.t - self.last_reset > 30*SHARED.steps_per_second:
            self.reset_temp_speed_limit()
        # TODO: mark on vehicle exit
        self.last_exit = SHARED.t

        raise NotImplementedError("TODO")
