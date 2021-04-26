from __future__ import annotations
from math import pi, sqrt, tan, ceil
from typing import List

# import bezier
# import numpy as np
# import scipy.optimize

from aimsim.util import Coord
from aimsim.trajectories import Trajectory


class BezierTrajectory(Trajectory):
    def __init__(self,
                 start_coord: Coord,
                 end_coord: Coord,
                 reference_coords: List[Coord],
                 traversibility_factors: List[float] = []) -> None:
        """Create a new quadratic Bezier trajectory.

        See trajectory.py for more information.

        Parameters specific to BezierTrajectory
            reference_coords: Iterable[Coord]
                A series of n coords that can be used for reference for any
                trajectory type. Preferred length depends on trajectory type.
        """
        super().__init__(
            start_coord=start_coord,
            end_coord=end_coord,
            reference_coords=reference_coords,
            traversibility_factors=traversibility_factors
        )
        assert len(reference_coords) == 1
        self.control_coord: Coord = reference_coords[0]

        self._length: float = self.__find_length()

    @classmethod
    def as_intersection_connector(cls,
                                  start_coord: Coord,
                                  start_heading: float,
                                  end_coord: Coord,
                                  end_heading: float) -> BezierTrajectory:
        """Creates a Bezier trajectory for use in an IntersectionLane.

        Calculates the control coordinate and traversibility from the given
        parameters then passes them to the constructor.
        """

        if start_heading % 2*pi == (end_heading + pi) % 2*pi:
            raise ValueError("IO lanes are parallel.")

        # Find the intersection of the two headings.
        # First, check the special cases for if either heading is vertical.
        control_coord: Coord
        start_vert = start_heading % pi == pi/2
        end_vert = end_heading % pi == pi/2
        if start_vert and end_vert:
            control_coord = Coord(end_coord.x, (end_coord.y - start_coord.y)/2)
        elif start_vert:
            control_coord = Coord(start_coord.x,
                                  tan(end_heading)*(start_coord.x-end_coord.x
                                                    ) + end_coord.y)
        elif end_vert:
            control_coord = Coord(end_coord.x,
                                  tan(start_heading)*(end_coord.x-start_coord.x
                                                      ) + start_coord.y)
        else:
            # If they aren't vertical find the intersection per this link.
            # https://math.stackexchange.com/questions/1990698/intersection-of-two-lines-each-defined-by-a-point-and-an-angle
            m0 = tan(start_heading)
            m1 = tan(end_heading)
            if m0 == m1 == 0:
                # Horizontal case
                control_coord = Coord((end_coord.x - start_coord.x)/2,
                                      end_coord.y)
            else:
                x = ((m0*start_coord.x - m1*end_coord.x) -
                     (start_coord.y - end_coord.y)) / (m0 - m1)
                y = m0*(x-start_coord.x) + start_coord.y
                control_coord = Coord(x, y)

        # TODO (low): Calculate traversibility factors.

        return cls(start_coord, end_coord,
                   reference_coords=[control_coord],
                   traversibility_factors=[])

    @property
    def length(self) -> float:
        return self._length

    @staticmethod
    def __quadratic_bezier(p: float, start: float, control: float, end: float
                           ) -> float:
        """Return the (1D, quadratic) Bezier coordinate of a proportion."""
        return (1-p)*((1-p)*start+p*control) + p*((1-p)*control+p*end)

    def __find_length(self, delta: float = 0.001) -> float:
        total: float = 0.
        last_point = self.get_position(0)
        for i in range(1, ceil(1/delta)+1):
            increment = i*delta
            increment = 1 if increment > 1 else increment

            next_point = self.get_position(increment)
            total += sqrt((next_point.x - last_point.x)**2 +
                          (next_point.y - last_point.y)**2)
            last_point = next_point
        return total

    def get_position(self, proportion: float) -> Coord:
        """Return the Coord associated with a proportional progress."""

        return Coord(
            self.__quadratic_bezier(proportion,
                                    self.start_coord.x,
                                    self.control_coord.x,
                                    self.end_coord.x),
            self.__quadratic_bezier(proportion,
                                    self.start_coord.y,
                                    self.control_coord.y,
                                    self.end_coord.y)
        )
