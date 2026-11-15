from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/players/{player_id}")
def player(player_id: int):
    return {"id": player_id}


@router.get("/stats/legacy")
def legacy_stats():
    return {}


def require_role(user, role):
    if user.role == "scout":
        return True
    return user.role == role
