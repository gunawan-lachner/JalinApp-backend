# SQL Queries for RNA Transaction
# Query for transaction table only
RNA_TRANSACTION_BASE_QUERY = '''
    SELECT  
    trx.id AS 'Trx ID',
    trx.tid AS 'TID',
    trx.uuid AS 'UUID',
    trx.amount AS 'Amount',
    trx.result AS 'Result',
    trx.participant_acq_id AS 'Acq ID',
    trx.participant_iss_id AS 'Iss ID',
    trx.msgclass as 'Msg Class',
    trx.msgtype as 'Msg Type',
    trx.status AS 'Trx Status',
    trx.refuuid AS 'Ref UUID',
    trx.reversaltries AS 'Reversal Tries',
    trx.dt AS 'Date n Time',
    trx.dttimeout AS 'Date n Time Time Out'
    FROM transaction trx FORCE INDEX (PRIMARY)
    WHERE {where_clause}
    AND `trx`.`participant_acq_id` IN (1702,1703,1704,1705)
    ORDER BY `trx`.`id` DESC 
    LIMIT {limit}
    OFFSET {offset}
'''

# Query for transaction_attr table with specific transaction IDs
RNA_TRANSACTION_ATTR_QUERY = '''
    SELECT
    transaction_id AS 'Trx ID',
    field,
    content
    FROM transaction_attr
    WHERE transaction_id IN ({transaction_ids})
'''

# Query to get transaction IDs when searching by attribute fields
RNA_TRANSACTION_ATTR_SEARCH_QUERY = '''
    SELECT DISTINCT
    transaction_id AS 'Trx ID'
    FROM transaction_attr
    WHERE {attr_where_clause}
    ORDER BY transaction_id DESC
    LIMIT {limit}
    OFFSET {offset}
'''

# Keep the original query for backward compatibility
RNA_TRANSACTION_QUERY = '''
    SELECT DISTINCT   
        trx.id AS 'Trx ID',  
        trx.tid AS 'TID',  
        trx.uuid AS 'UUID',  
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'OR.RRN' LIMIT 1) AS RRN,  
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.MASK_PAN' LIMIT 1) AS 'Card Number',  
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.PROCCODE' LIMIT 1) AS 'Procode',  
        trx.amount AS 'Amount',  
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IR.RC' LIMIT 1) AS 'RC Host',  
        trx.result AS 'Result',  
        trx.participant_acq_id AS 'Acq ID',  
        trx.participant_iss_id AS 'Iss ID',  
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.GATEWAY_ID' LIMIT 1) AS 'Gateway ID',  
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IR.PRINCIPALS_CODE' LIMIT 1) AS 'Principal Code',  
        trx.msgclass AS 'Msg Class',  
        trx.msgtype AS 'Msg Type',  
        trx.status AS 'Trx Status',  
        trx.refuuid AS 'Ref UUID',  
        trx.reversaltries AS 'Reversal Tries',  
        (trx.dt + INTERVAL 7 HOUR) AS 'Date n Time',  
        (trx.dttimeout + INTERVAL 7 HOUR) AS 'Date n Time Time Out',
        -- Add participant names
        (SELECT name FROM participant WHERE id = trx.participant_acq_id LIMIT 1) AS 'Acquier Name',
        (SELECT name FROM participant WHERE id = trx.participant_iss_id LIMIT 1) AS 'Issuer Name',
        -- Add descriptions
        (SELECT description FROM transaction_result WHERE id = trx.result LIMIT 1) AS 'Result Desc',
        (SELECT description FROM cfg_msgtype WHERE id = trx.msgtype LIMIT 1) AS 'Msg Type Description',
        (SELECT description FROM cfg_msgclass WHERE id = trx.msgclass LIMIT 1) AS 'Msg Class Description',
        (SELECT description FROM transaction_status WHERE id = trx.status LIMIT 1) AS 'Trx Status Description'
    FROM   
        transaction trx  
    WHERE {where_clause}
        AND `trx`.`participant_acq_id` IN (1702,1703,1704,1705) 
    ORDER BY `trx`.`id` DESC 
    LIMIT {limit} OFFSET {offset}
'''

RNA_TRANSACTION_QUERY_X = '''
    SELECT DISTINCT   
        trx.id AS 'Trx ID',  
        trx.tid AS 'TID',  
        trx.uuid AS 'UUID',  

        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.RRN' LIMIT 1) AS RRN,  

        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.MASK_PAN' LIMIT 1) AS 'Card Number',  

        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.PROCCODE' LIMIT 1) AS 'Procode',  

        trx.amount AS 'Amount',  

        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IR.RC' LIMIT 1) AS 'RC Host',  
        
        (SELECT mapto FROM conversion con
        WHERE con.type_id = 10007 AND con.plugin_id = 505 AND con.mapfrom = CAST(trx.result AS CHAR)) AS 'RC EFTS',  
                
        trx.result AS 'Result',  
        trx.participant_acq_id AS 'Acq ID',  
        trx.participant_iss_id AS 'Iss ID',  

        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.GATEWAY_ID' LIMIT 1) AS 'Gateway ID',  

        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IR.PRINCIPALS_CODE' LIMIT 1) AS 'Principal Code',  

        trx.msgclass AS 'Msg Class',  
        trx.msgtype AS 'Msg Type',  
        trx.status AS 'Trx Status',  
        trx.refuuid AS 'Ref UUID',  
        trx.reversaltries AS 'Reversal Tries',  
        (trx.dt + INTERVAL 7 HOUR) AS 'Date n Time',  
        (trx.dttimeout + INTERVAL 7 HOUR) AS 'Date n Time Time Out',  

        -- Subqueries for participant names  
        (SELECT name FROM participant WHERE id = trx.participant_acq_id LIMIT 1) AS AcqName,  
        (SELECT name FROM participant WHERE id = trx.participant_iss_id LIMIT 1) AS IssName,  
        
        -- Transaction attributes  
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'OT.RECEIVING_INSTITUTION_ID' LIMIT 1) AS IssId,  
        
        (SELECT content FROM transaction_attr   
        WHERE transaction_id = trx.id AND field = 'IN.BID' LIMIT 1) AS DestID,  
        
        (SELECT description FROM transaction_result WHERE id = trx.result LIMIT 1) AS DescResult,  
        
        (SELECT description FROM cfg_msgtype WHERE id = trx.msgtype LIMIT 1) AS TypeTrx  

    FROM   
        transaction trx  

    -- Left Joins for additional filtering  
    LEFT JOIN transaction_attr mask_pan  
        ON trx.id = mask_pan.transaction_id   
        AND mask_pan.field = 'IN.MASK_PAN'   

    LEFT JOIN transaction_attr rrn  
        ON trx.id = rrn.transaction_id   
        AND rrn.field = 'OR.RRN'  

    LEFT JOIN transaction_attr operator  
        ON trx.id = operator.transaction_id   
        AND operator.field = 'IN.OPERATOR_ID'  

    WHERE {where_clause}
        AND `trx`.`participant_acq_id` IN (1702,1703,1704,1705) 
    GROUP BY `trx`.`id`  
    ORDER BY `trx`.`id` DESC 
    LIMIT {limit} OFFSET {offset}
'''

RNA_TRANSACTION_QUERY_X = '''
    SELECT  
    trx.id AS 'Trx ID',  
    trx.tid AS 'TID',
    trx.uuid AS 'UUID',  
    CAST(trx_attr.content AS CHAR) AS RRN,  
    pan.content AS 'Card Number',  
    pcode.content AS 'Procode',  
    trx.amount AS 'Amount',  
    rcin.content AS 'RC Host',  
    trx.result AS 'Result',  
    trx.participant_acq_id AS 'Acq ID',  
    trx.participant_iss_id AS 'Iss ID',  
    gateway_id.content AS 'Gateway ID',  
    prccode.content AS 'Principal Code',
    trx.msgclass as 'Msg Class',  
    trx.msgtype as 'Msg Type',  
    trx.status AS 'Trx Status',  
    trx.refuuid AS 'Ref UUID',  
    trx.reversaltries AS 'Reversal Tries',  
    trx.dt AS 'Date n Time',  
    trx.dttimeout AS 'Date n Time Time Out'
    FROM transaction trx FORCE INDEX (PRIMARY)  
    LEFT JOIN transaction_attr trx_attr ON trx.id = trx_attr.transaction_id AND trx_attr.field = 'OR.RRN'  
    LEFT JOIN transaction_attr rcin ON trx.id = rcin.transaction_id AND rcin.field = 'IR.RC'  
    LEFT JOIN transaction_attr pcode ON trx.id = pcode.transaction_id AND pcode.field = 'IN.PROCCODE'  
    LEFT JOIN transaction_attr prccode ON trx.id = prccode.transaction_id AND prccode.field = 'IR.PRINCIPALS_CODE'  
    LEFT JOIN transaction_attr pan ON trx.id = pan.transaction_id AND pan.field = 'IN.MASK_PAN'  
    LEFT JOIN transaction_attr gateway_id ON trx.id = gateway_id.transaction_id AND gateway_id.field = 'IN.GATEWAY_ID'  
    WHERE {where_clause}
    AND `trx`.`participant_acq_id` IN (1702,1703,1704,1705) 
    GROUP BY `trx`.`id`  
    ORDER BY `trx`.`id` DESC 
    LIMIT {limit}
    OFFSET {offset}
'''

# Field mapping to determine which table to search in
TRANSACTION_FIELDS = [
    'Trx ID', 'TID', 'UUID', 'Amount', 'Result', 'Acq ID', 'Iss ID', 
    'Msg Class', 'Msg Type', 'Trx Status', 'Ref UUID', 'Reversal Tries', 
    'Date n Time', 'Date n Time Time Out'
]

TRANSACTION_ATTR_FIELDS = {
    'RRN': 'OR.RRN',
    'Card Number': 'IN.MASK_PAN',
    'Procode': 'IN.PROCCODE',
    'RC Host': 'IR.RC',
    'Gateway ID': 'IN.GATEWAY_ID',
    'Principal Code': 'IR.PRINCIPALS_CODE'
}

# Default values for query parameters
DEFAULT_QUERY_PARAMS = {
    'limit': 500,
    'offset': 0,
    'where_clause': '1=1'  # Default condition that always evaluates to true
} 

# The original query for reference (will be used for full exports but not display)
RNA_TRANSACTION_FULL_QUERY = '''
    @@SELECT  
    trx.id AS 'Trx ID',  
    trx.tid AS 'TID',
    trx.uuid AS 'UUID',  
    CAST(trx_attr.content AS CHAR) AS RRN,  
    pan.content AS 'Card Number',  
    pcode.content AS 'Procode',  
    trx.amount AS 'Amount',  
    rcin.content AS 'RC Host',  
    trx.result AS 'Result',  
    con.mapto AS 'RC EFTS', 
    res.description AS 'Result Desc',  
    trx.participant_acq_id AS 'Acq ID',  
    acq.name AS 'Acquier Name',  
    trx.participant_iss_id AS 'Iss ID',  
    iss.name AS 'Issuer Name',  
    gateway_id.content AS 'Gateway ID',  
    gateway_name.name AS 'Gateway Name',  
    prccode.content AS 'Principal Code',
    trx.msgclass as 'Msg Class',  
    msgclass.description AS 'Msg Class Description',  
    trx.msgtype as 'Msg Type',  
    msgtype.description AS 'Msg Type Description',  
    trx.status AS 'Trx Status',  
    trx_status.description AS 'Trx Status Description',
    trx.refuuid AS 'Ref UUID',  
    trx.reversaltries AS 'Reversal Tries',  
    trx.dt AS 'Date n Time',  
    trx.dttimeout AS 'Date n Time Time Out'
    FROM transaction trx FORCE INDEX (PRIMARY)  
    INNER JOIN transaction_result res ON trx.result = res.id  
    INNER JOIN transaction_attr trx_attr ON trx.id = trx_attr.transaction_id AND trx_attr.field = 'OR.RRN'  
    INNER JOIN transaction_attr rcin ON trx.id = rcin.transaction_id AND rcin.field = 'IR.RC'  
    INNER JOIN transaction_attr pcode ON trx.id = pcode.transaction_id AND pcode.field = 'IN.PROCCODE'  
    INNER JOIN transaction_attr prccode ON trx.id = prccode.transaction_id AND prccode.field = 'IR.PRINCIPALS_CODE'  
    INNER JOIN transaction_attr pan ON trx.id = pan.transaction_id AND pan.field = 'IN.MASK_PAN'  
    INNER JOIN transaction_attr gateway_id ON trx.id = gateway_id.transaction_id AND gateway_id.field = 'IN.GATEWAY_ID'  
    INNER JOIN bin_gateway gateway_name ON gateway_id.content = gateway_name.id  
    INNER JOIN conversion con ON con.type_id = 10007 AND con.plugin_id = 505 AND con.mapfrom = CAST(trx.result AS CHAR)  
    INNER JOIN participant acq ON trx.participant_acq_id = acq.id  
    INNER JOIN participant iss ON trx.participant_iss_id = iss.id  
    INNER JOIN cfg_msgclass msgclass ON trx.msgclass = msgclass.id  
    INNER JOIN cfg_msgtype msgtype ON trx.msgtype = msgtype.id  
    INNER JOIN transaction_status trx_status ON trx.status = trx_status.id  
    WHERE {where_clause}
    AND `trx`.`participant_acq_id` IN (1702,1703,1704,1705) 
    GROUP BY `trx`.`id`  
    ORDER BY `trx`.`id` DESC 
    LIMIT {limit}
'''

TRANSACTION_ENDPOINT_DATA_QUERY = '''
    SELECT
        id,
        uuid,
        dt,
        msg_direction,
        request_response,
        message,
        participant_id,
        state,
        worker_id
    FROM transaction_endpoint_data
    WHERE uuid = %s
    ORDER BY dt ASC
'''