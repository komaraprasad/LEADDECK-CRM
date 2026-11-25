from app.mappers import talentpulse, hiringbadge, commentminer, jobharvest

MAPPERS = {
    "talentpulse": talentpulse.map_payload,
    "hiringbadge": hiringbadge.map_payload,
    "commentminer": commentminer.map_payload,
    "jobharvest": jobharvest.map_payload,
}
