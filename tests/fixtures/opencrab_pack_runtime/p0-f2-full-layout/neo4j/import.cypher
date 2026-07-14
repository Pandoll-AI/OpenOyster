// P0-F2 full-layout Neo4j import for pack p0-f2-full-layout
// Canonical graph: resource:doc:1 -[:contains]-> evidence:1 -[:supports]-> claim:1
CREATE CONSTRAINT opencrab_node_id IF NOT EXISTS FOR (n:OpenCrabNode) REQUIRE n.id IS UNIQUE;

MERGE (doc:OpenCrabNode:Document {id: 'resource:doc:1'})
SET doc.label = 'Source Document',
    doc.space = 'resource',
    doc.node_type = 'Document',
    doc.pack_id = 'p0-f2-full-layout',
    doc.status = 'validated',
    doc.evidence_refs = ['evidence:1'];

MERGE (ev:OpenCrabNode:Evidence {id: 'evidence:1'})
SET ev.label = 'Evidence 1',
    ev.space = 'evidence',
    ev.node_type = 'Evidence',
    ev.pack_id = 'p0-f2-full-layout',
    ev.status = 'validated',
    ev.evidence_refs = ['evidence:1'];

MERGE (claim:OpenCrabNode:Claim {id: 'claim:1'})
SET claim.label = 'Supported claim',
    claim.space = 'claim',
    claim.node_type = 'Claim',
    claim.pack_id = 'p0-f2-full-layout',
    claim.statement = 'The source supports this claim.',
    claim.status = 'validated',
    claim.confidence = 0.9,
    claim.evidence_refs = ['evidence:1'];

MATCH (doc:OpenCrabNode {id: 'resource:doc:1'}), (ev:OpenCrabNode {id: 'evidence:1'})
MERGE (doc)-[r:CONTAINS {id: 'edge:contains:1'}]->(ev)
SET r.from_space = 'resource',
    r.to_space = 'evidence',
    r.relation = 'contains',
    r.confidence = 1.0,
    r.evidence_refs = ['evidence:1'],
    r.pack_id = 'p0-f2-full-layout';

MATCH (ev:OpenCrabNode {id: 'evidence:1'}), (claim:OpenCrabNode {id: 'claim:1'})
MERGE (ev)-[s:SUPPORTS {id: 'edge:supports:1'}]->(claim)
SET s.from_space = 'evidence',
    s.to_space = 'claim',
    s.relation = 'supports',
    s.confidence = 0.9,
    s.evidence_refs = ['evidence:1'],
    s.pack_id = 'p0-f2-full-layout';
