// Device rings: 3 groups of 8 customers sharing a device
UNWIND [
  {prefix: "C1_", device_id: "DEV_1"},
  {prefix: "C2_", device_id: "DEV_2"},
  {prefix: "C3_", device_id: "DEV_3"}
] AS ring
UNWIND range(1, 8) AS i
MERGE (c:Customer {id: ring.prefix + toString(i)})
MERGE (d:Device {id: ring.device_id})
MERGE (c)-[:USES_DEVICE]->(d);

// Isolate customers: kept out of shared-device projections
UNWIND [
  {customer_id: "ISOLATE_C1", device_id: "DEV_ISO_1"},
  {customer_id: "ISOLATE_C2", device_id: "DEV_ISO_2"},
  {customer_id: "ISOLATE_C3", device_id: "DEV_ISO_3"},
  {customer_id: "ISOLATE_C4", device_id: "DEV_ISO_4"}
] AS row
MERGE (c:Customer {id: row.customer_id})
MERGE (d:Device {id: row.device_id})
MERGE (c)-[:USES_DEVICE]->(d);

// Shared cards for account-takeover style motifs
UNWIND [
  {card_id: "CARD_1", customers: ["C1_1", "C1_2", "C1_3"]},
  {card_id: "CARD_2", customers: ["C2_1", "C2_2", "C2_3"]},
  {card_id: "CARD_3", customers: ["C3_1", "C3_2"]},
  {card_id: "CARD_ISO_1", customers: ["ISOLATE_C1"]}
] AS row
MERGE (card:Card {id: row.card_id})
WITH row, card
UNWIND row.customers AS customer_id
MATCH (c:Customer {id: customer_id})
MERGE (c)-[:USES_CARD]->(card);

// Shared IP cohorts, including one cross-ring IP to create a mixed cluster
UNWIND [
  {ip_id: "IP_1", customers: ["C1_1", "C1_2", "C1_3"]},
  {ip_id: "IP_2", customers: ["C2_1", "C2_2", "C3_1"]},
  {ip_id: "IP_3", customers: ["C3_2", "C3_3"]},
  {ip_id: "IP_ISO_1", customers: ["ISOLATE_C1"]}
] AS row
MERGE (ip:IP {id: row.ip_id})
WITH row, ip
UNWIND row.customers AS customer_id
MATCH (c:Customer {id: customer_id})
MERGE (c)-[:USES_IP]->(ip);

// Merchants and chargeback-prone customers
UNWIND ["M1", "M2", "M3", "M4"] AS merchant_id
MERGE (:Merchant {id: merchant_id});

UNWIND ["C1_1", "C2_1", "C3_1", "C3_2"] AS customer_id
MATCH (c:Customer {id: customer_id})
SET c.charged_back = true;

UNWIND [
  {customer_id: "C1_1", merchant_id: "M1"},
  {customer_id: "C1_1", merchant_id: "M2"},
  {customer_id: "C2_1", merchant_id: "M1"},
  {customer_id: "C2_1", merchant_id: "M2"},
  {customer_id: "C3_1", merchant_id: "M2"},
  {customer_id: "C3_1", merchant_id: "M3"},
  {customer_id: "C3_2", merchant_id: "M2"},
  {customer_id: "C3_2", merchant_id: "M3"},
  {customer_id: "C1_2", merchant_id: "M4"}
] AS row
MATCH (c:Customer {id: row.customer_id})
MATCH (m:Merchant {id: row.merchant_id})
MERGE (c)-[:TRANSACTED_AT]->(m);

// Payout accounts and countries for shell-company style clusters
UNWIND [
  {merchant_id: "M1", payout_account_id: "PA_SHARED", country_id: "GB"},
  {merchant_id: "M2", payout_account_id: "PA_SHARED", country_id: "GB"},
  {merchant_id: "M4", payout_account_id: "PA_SHARED", country_id: "GB"},
  {merchant_id: "M3", payout_account_id: "PA_OFFSHORE", country_id: "CY"}
] AS row
MATCH (m:Merchant {id: row.merchant_id})
MERGE (payout:PayoutAccount {id: row.payout_account_id})
MERGE (country:Country {id: row.country_id})
MERGE (m)-[:PAYOUT_TO]->(payout)
MERGE (payout)-[:REGISTERED_IN]->(country);

// Money-flow graph
UNWIND [
  {id: "TX1", amount: 500.0, acquirer_id: "ACQ_1", issuer_id: "ISS_1", decline_count: 3},
  {id: "TX2", amount: 250.0, acquirer_id: "ACQ_1", issuer_id: "ISS_1", decline_count: 2},
  {id: "TX3", amount: 750.0, acquirer_id: "ACQ_2", issuer_id: "ISS_1", decline_count: 0},
  {id: "TX4", amount: 100.0, acquirer_id: "ACQ_2", issuer_id: "ISS_1", decline_count: 4},
  {id: "TX5", amount: 350.0, acquirer_id: "ACQ_3", issuer_id: "ISS_2", decline_count: 1},
  {id: "TX6", amount: 600.0, acquirer_id: "ACQ_3", issuer_id: "ISS_2", decline_count: 0}
] AS row
MERGE (transaction:Transaction {id: row.id, amount: row.amount})
MERGE (acquirer:Acquirer {id: row.acquirer_id})
MERGE (issuer:Issuer {id: row.issuer_id})
MERGE (transaction)-[:ROUTED_TO {decline_count: row.decline_count}]->(acquirer)
MERGE (acquirer)-[:SETTLES_WITH]->(issuer);
