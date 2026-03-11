from fastapi import APIRouter

router = APIRouter()

@router.post("/trigger")
async def trigger_pipeline():
    """
    Trigger the RAG data collection pipeline manually.
    """
    # Fetch data
    # Save to Tier 1
    # Publish to Redis Queue
    return {"status": "Pipeline triggered successfully"}

@router.get("/status")
async def get_pipeline_status():
    """
    Get the status of the data collection pipeline from Tier 3 RDB
    """
    return {"status": "Pending"}
