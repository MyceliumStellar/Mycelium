"""
Test Case 02: Complex Structs and Internal Calls.
Tests nested struct access and internal function mutability.
"""

@struct
class Point:
    x: uint256
    y: uint256

@struct
class Shape:
    points: DynArray[Point, 4]
    name: String[32]

@internal
@pure
def _dist_sq(p1: Point, p2: Point) -> uint256:
    dx: uint256 = 0
    if p1.x > p2.x:
        dx = p1.x - p2.x
    else:
        dx = p2.x - p1.x
    
    dy: uint256 = 0
    if p1.y > p2.y:
        dy = p1.y - p2.y
    else:
        dy = p2.y - p1.y
        
    return dx * dx + dy * dy

@external
@view
def get_shape_width(s: Shape) -> uint256:
    # Call internal pure function
    return self._dist_sq(s.points[0], s.points[1])

@external
@pure
def create_point(x: uint256, y: uint256) -> Point:
    p: Point = Point(x=x, y=y)
    return p
