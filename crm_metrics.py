"""Shared pending-message boundary for inbox timers and operational reports."""

THREADS_CTE = """WITH visible AS (
    SELECT m.rowid seq,m.*,row_number() OVER(PARTITION BY m.channel_id,m.contact_id
        ORDER BY m.created_at DESC,m.rowid DESC) newest
    FROM messages m JOIN channels ch ON ch.id=m.channel_id
    WHERE ch.doctor_id=? AND ((?='' AND ch.archived_at=0) OR ch.id=?) AND
    (m.direction='in' OR m.status IN ('sent','sending','unknown') OR (m.campaign_id IS NULL AND m.status='queued'))
), marked AS (
    SELECT v.*,CASE WHEN v.direction='in' AND v.seq>coalesce(r.resolved_through,0)
        AND coalesce(nullif(v.provider_timestamp,0),v.created_at)>=coalesce(r.external_reply_at,0)
        THEN 1 ELSE 0 END pending
    FROM visible v LEFT JOIN conversation_resolutions r ON r.channel_id=v.channel_id AND r.contact_id=v.contact_id
), threads AS (
    SELECT channel_id,contact_id,max(CASE WHEN newest=1 THEN seq END) latest,sum(pending) unread,
        min(CASE WHEN pending=1 THEN coalesce(nullif(provider_timestamp,0),created_at) END) waiting_since
    FROM marked GROUP BY channel_id,contact_id
) """
