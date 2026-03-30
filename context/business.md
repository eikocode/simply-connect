# Business Context

<!-- Admin-controlled. Intake target: AIOS context/business-info.md -->
<!-- Covers: org name, business type, jurisdiction, how contracts are structured here -->
<!-- Only modify via sc-admin review after staging approval -->

## Organization

[empty — populate via `sc-admin intake` or admin edit]

## Contract Structure

[empty — describe how this business typically structures contracts: standard terms, governing law, preferred formats]

## Key Facts

[empty — stage of business, team size, constraints Claude should know when working on contracts]


<!-- Committed 2026-03-26 05:04 UTC -->
Agreement No.: HSA-2026-001-HK
Effective Date: January 14, 2026
Buyer: Memora Technologies Limited, 18/F Tower One, Lippo Centre, 89 Queensway, Admiralty, Hong Kong
Signed by: Elena Rossi, CTO
Supplier: Precision Components Ltd., 10 Science Park Road, #02-01 The Alpha, Singapore 117684
Signed by: David Tan, Director of Sales
Governing Law: Hong Kong SAR


<!-- Committed 2026-03-26 05:04 UTC -->
Optical Sensor Module (OS-2026): USD $45.00/unit, MOQ 1,000 units, 8-week lead time
Link Device Enclosure (LD-115): USD $22.50/unit, MOQ 5,000 units, 6-week lead time
Prices fixed in USD, exclusive of HK taxes/duties/import fees (borne by Buyer)


<!-- Committed 2026-03-26 05:04 UTC -->
Supplier must not disclose Buyer's proprietary information including:
- Hybrid storage architecture (local temp → secure primary → auto-erasure)
- Algorithm integration with persona models and multimodal interaction
- Technical specs for optical sensors and link devices
Confidentiality obligations survive termination for 5 years


<!-- Committed 2026-03-26 05:36 UTC -->
All inventions, designs, improvements, and works of authorship created by Supplier under this Agreement are solely owned by Buyer, including modifications to secure link device architecture and proximity-triggered data synchronization logic.
Supplier retains Background IP but grants Buyer perpetual, irrevocable, royalty-free, worldwide license for embedded Background IP.
Supplier assigns all derivative works, improvements, and adaptations of link device architecture to Buyer.


<!-- Committed 2026-03-26 05:37 UTC -->
Supplier liability cap: USD $500,000 or 12 months of fees paid, whichever is less
Exclusions from cap: confidentiality breaches, IP infringement, gross negligence
Neither party liable for indirect, incidental, special, or consequential damages (including lost profits or data)


<!-- Committed 2026-03-26 05:37 UTC -->
Payment terms: Net 30 days from invoice date
Late payment interest: 1.5% per month


<!-- Committed 2026-03-26 05:37 UTC -->
Initial term: 3 years from January 14, 2026 (expires January 14, 2029)
Auto-renews for 1-year periods unless 90 days written notice of non-renewal
Termination for material breach: 30 days written notice with cure opportunity


<!-- Committed 2026-03-26 05:37 UTC -->
Model: LD-115
Dimensions: 100mm x 100mm x 30mm
Material: Aluminum alloy, IP54 rated
I/O: ZERO external ports, buttons, screens, speakers, or microphones
Storage access: Internal NVMe slot via proprietary tool only
Antenna: Integrated Nordic nRF52840 compatible (2.4GHz PAN)
Security: Tamper-evident seals on all enclosure seams


<!-- Committed 2026-03-26 05:37 UTC -->
Model: OS-2026
Sensor: Global Shutter CMOS (OmniVision OV9282 equivalent)
Resolution: 1280 x 800 px
Frame rate: 60 fps minimum
Latency: ≤50ms end-to-end for position tracking
Accuracy: ±2cm position tracking; ≥95% facial expression confidence
Interface: MIPI CSI-2
Power: ≤500mW average
Operating temp: 0°C to 45°C


<!-- Committed 2026-03-26 05:37 UTC -->
Agreement No.: IPL-2026-002-HK
Effective Date: January 14, 2026
Licensor: Cognitive Algorithms Inc., incorporated in Delaware, USA; 123 Innovation Drive, Palo Alto, CA 94301, USA
Licensee: Memora Technologies Limited, incorporated in Hong Kong; 18/F, Tower One, Lippo Centre, 89 Queensway, Admiralty, Hong Kong
Governing Law: Hong Kong Special Administrative Region


<!-- Committed 2026-03-26 05:37 UTC -->
Term: 5 years from January 14, 2026; auto-renews for 2-year periods unless 180 days' written notice of non-renewal.
Termination for breach: 60 days' written notice with cure opportunity.
IP ownership: Licensee owns all improvements, modifications, and integrations it creates; no joint ownership.
Licability cap: 12 months' royalties paid/payable preceding the claim (excludes confidentiality breaches, IP infringement, indemnity obligations). No indirect/consequential damages.
Confidentiality survives termination for 5 years.
Audit rights: Licensor may audit once per calendar year with 30 days' written notice.


<!-- Committed 2026-03-26 05:37 UTC -->
License type: Non-exclusive, non-transferable, royalty-bearing.
Restrictions: No sublicensing or distribution to third parties without Licensor's prior written consent. No reverse engineering/decompilation except as permitted by HK law. No use outside Field of Use without separate written agreement.


<!-- Committed 2026-03-26 05:38 UTC -->
Royalty rate: 8% of Net Revenue from products/services incorporating the Licensed IP.
Minimum annual royalty: USD $50,000, payable in advance on each anniversary of Effective Date.
Royalty reports and payments due quarterly within 30 days of quarter end.
All amounts in USD.
Sample quarterly amounts (from Exhibit B template): MC-110 $40,000, LD-115 $16,000, Subscriptions $8,000 (on $800,000 net revenue).


<!-- Committed 2026-03-26 05:38 UTC -->
Licensed IP: Spaced-repetition algorithm (ASRE v3.1) — difficulty-stability-retrievability (DSR) model with 19 parameter optimization logic.
Field of Use: Cognitive support applications for elderly individuals — memory training, personalized interaction, wellness monitoring — using smart companion hardware (MC-110) and link devices (LD-115).
Core variables: Retrievability (R, 0.0–1.0), Stability (S, days for R to decay 100%→90%), Difficulty (D, 1.0–10.0).
Parameters derived from 220M+ review logs (Public Dataset Ref: KDD-2022-YE).


<!-- Committed 2026-03-26 05:38 UTC -->
Biometric data collected: facial recognition, optical position tracking.
Purposes: user authentication, real-time animation alignment, mood/emotional state detection.
Consent: explicit, given by use of the Service.
Withdrawal: via Settings at any time; may limit Service functionality.


<!-- Committed 2026-03-26 05:38 UTC -->
- Local storage on MC-110: max 24 hours retention
- Sync to LD-115 when within ≤3 meters, encrypted with AES-256
- Automatic erasure from MC-110 within 5 seconds of successful sync confirmation
- Primary datastore resides on LD-115 (no direct I/O access)
- User data erased from primary datastore upon account termination + written request, subject to legal retention


<!-- Committed 2026-03-26 05:38 UTC -->
Governing law: Hong Kong Special Administrative Region.
Dispute resolution: exclusive jurisdiction of Hong Kong courts.
No arbitration clause — court-based resolution.


<!-- Committed 2026-03-26 05:38 UTC -->
Smart Companion device: Model MC-110 (local temporary storage, interaction interface)
Link device: Model LD-115 (primary datastore, no I/O ports, proximity sync target)
Proximity range for sync: ≤3 meters


<!-- Committed 2026-03-26 05:38 UTC -->
- Company does not claim IP ownership over user personal data, memory cards, conversation history, or biometric inputs.
- User retains all rights to personal data.
- Service software, algorithms, designs, and content owned by Company or licensors.
- License granted: limited, non-exclusive, non-transferable, revocable, personal non-commercial use only.


<!-- Committed 2026-03-26 05:38 UTC -->
Liability cap: USD $100 or amount paid in preceding 12 months, whichever is greater.
No liability for indirect, incidental, special, consequential, or punitive damages.
Service provided 'as is' with no warranties.
Not a medical device; not a substitute for professional medical advice.


<!-- Committed 2026-03-26 05:38 UTC -->
Provider: Memora Technologies Limited, incorporated in Hong Kong.
Service name: Adaptive Cognitive Support Companion.
Governing law: Hong Kong Special Administrative Region.
Support contact: support@memora.tech
Privacy Policy: https://www.memora.tech/privacy
Effective Date: January 14, 2026 (Version 2.1)


<!-- Committed 2026-03-26 05:39 UTC -->
Technical Information: Hybrid storage architecture designs, optical sensor specs (Model OS-2026), link device configs (Model LD-115), DSR algorithm integration logic, persona model structures, interaction engine workflows
Business Information: Product roadmap (Memora MC-110 Launch Q3 2026), pricing strategies, supplier lists (Precision Components Ltd.), customer data, financial projections, marketing plans


<!-- Committed 2026-03-26 05:39 UTC -->
Effective Date: January 14, 2026
Agreement Term: 3 years from effective date
Confidentiality Survival: 5 years from date of disclosure of relevant information
Early termination: by mutual written agreement


<!-- Committed 2026-03-26 05:39 UTC -->
Key obligations:
- Use Confidential Information solely for the Purpose
- No third-party disclosure without prior written consent
- Protect with at least reasonable care
- Limit access to need-to-know personnel bound by confidentiality

Liability cap: USD $100,000 (except for breaches of confidentiality)
No indirect, incidental, special, or consequential damages

Remedies for breach: injunctive relief, specific performance, and equitable remedies available


<!-- Committed 2026-03-26 05:39 UTC -->
Agreement No.: NDA-2026-004-HK
Disclosing Party: Memora Technologies Limited, 18/F, Tower One, Lippo Centre, 89 Queensway, Admiralty, Hong Kong
Receiving Party: CareFacility Partners Ltd., 5/F, Health Plaza, 100 Wong Chuk Hang Road, Hong Kong
Governing Law: Hong Kong Special Administrative Region


<!-- Committed 2026-03-26 05:39 UTC -->
Purpose: Potential deployment of Memora Smart Companions in CareFacility Partners Ltd. homes
Scope: Mutual NDA covering technical and business information exchanged during evaluation of potential business relationship


<!-- Committed 2026-03-26 05:39 UTC -->
Agreement: Data Processing Agreement DPA-2026-005-HK
Services: Secure Cloud Backup for Link Device Primary Datastores
Supplements: Main services agreement dated January 14, 2026
Compliance frameworks: Hong Kong PDPO (Cap. 486) and EU GDPR where applicable

Processor key obligations:
- Process data only on Controller's documented instructions
- Confidentiality obligations on all authorized persons
- Encryption in transit and at rest
- Access controls: authenticated Smart Companion devices only
- Automatic erasure of temporary local storage after successful sync
- Quarterly penetration testing
- Data breach notification to Controller within 24 hours of awareness
- No subprocessors without prior written Controller authorization
- Audits: max once per year (unless breach suspected), reasonable notice, business hours
- On termination: delete or return all Personal Data at Controller's choice


<!-- Committed 2026-03-26 05:39 UTC -->
Agreement No.: DPA-2026-005-HK
Effective Date: January 14, 2026
Data Controller: Memora Technologies Limited
  - Incorporated in Hong Kong
  - Address: 18/F, Tower One, Lippo Centre, 89 Queensway, Admiralty, Hong Kong
Data Processor: CloudSecure Services Ltd.
  - Incorporated in Ireland
  - Address: 2 Dublin Landings, North Wall Quay, Dublin 1, Ireland
Governing Law: Hong Kong Special Administrative Region


<!-- Committed 2026-03-26 05:39 UTC -->
Company: Memora Technologies Limited
Jurisdiction: Hong Kong
Address: 18/F, Tower One, Lippo Centre, 89 Queensway, Admiralty, Hong Kong
Product: Memora Smart Companion Service — AI companion device for elderly users (65+)
Device identifiers referenced: MC-110, LD-115
Data activities: cloud backup of primary datastores, biometric and health data processing


<!-- Committed 2026-03-26 05:40 UTC -->
Data Subjects: End users of Memora Smart Companion Service, including elderly individuals (65+)

Personal Data Categories:
- Identity Data (Standard): User ID, device identifiers (MC-110/LD-115 Serial)
- Biometric Data (Special Category): Facial recognition templates, optical position logs
- Health & Wellness Data (Special Category): Medication adherence, wellness goals, cognitive performance metrics
- Interaction Data (Standard): Conversation history, animation preferences, review performance
- Technical Data (Standard): IP address, device type, sync logs

Processing activities: collection, storage, synchronization, encryption, automatic erasure, analytics, security monitoring


<!-- Committed 2026-03-26 09:45 UTC -->
Memora Technologies Limited requires all employees to sign an IP Assignment Agreement (EIPA series) as a condition of employment. All inventions, software, algorithms, and works created during employment or using Company resources are assigned to Memora. Pre-existing IP must be declared in Exhibit A or it is deemed not to exist. Excluded IP used in Company products is licensed back on a non-exclusive, royalty-free, worldwide basis for internal use. Post-termination cooperation on patent prosecution is required at Company's expense.


<!-- Committed 2026-03-26 09:45 UTC -->
Work Product explicitly covered under EIPA-2026-006-HK includes:
- Improvements to hybrid storage architecture (peripheral datastore on smart companion → primary datastore on link device → automatic erasure upon sync confirmation)
- Link device designs without input/output elements (no screen, buttons, ports, speakers) for security
- Spaced repetition scheduling algorithms integrated with persona models, optical sensor position tracking, or multimodal animation synchronization
- Firmware for PAN-triggered synchronization (Bluetooth Low Energy / IEEE 802.15.4) with proximity-based authentication
- Persona model structures (relationship stage, personality, mood, biographical, wellness components) and refinement logic
- Technical specifications, UI designs, and manufacturing processes for Memora Smart Companion (Model MC-110) and Link Device (Model LD-115)


<!-- Committed 2026-03-26 10:23 UTC -->
Smart Companion (Model MC-110): Edge interaction unit with optical sensor, speaker, display, and actuatable element for conversation and animation.
Link Device (Model LD-115): Secure primary datastore hub with no input/output elements; proximity-based synchronization via Personal Area Network (PAN); sync range ≤ 3 meters.
Software Services: Interaction engine, persona modeling, spaced repetition algorithm, hybrid storage management, secure cloud backup.
Data Architecture: Local storage on MC-110 (max 24 hours) → AES-256 encrypted sync to LD-115 → automatic erasure from MC-110 within 5 seconds of confirmed sync.


<!-- Committed 2026-03-26 10:23 UTC -->
Customer obligations: Ensure Link Devices charged and within PAN range of Smart Companions at least once daily; obtain explicit consent from end users or legal guardians for biometric data (facial recognition, optical position tracking); comply with GDPR, PDPO, HIPAA (if applicable).
Provider obligations: Encrypt data in transit and at rest (AES-256); provide audit logs for sync events and erasure confirmations; notify Customer of data breaches within 24 hours.
Separate Data Processing Agreement (DPA) required as Exhibit B, compliant with GDPR/PDPO.
Biometric data collected: facial recognition, optical position tracking.
