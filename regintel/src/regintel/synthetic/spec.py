"""
Synthetic control framework for Bharat Unity Bank (FICTIONAL).
Single source of truth for policy text, controls, evidence expectations and the
regulatory links we EXPECT the system to discover. Regulation hints are pointers,
not quotes: verify against official texts.
"""
from __future__ import annotations

from datetime import date

from pydantic import Field

from regintel.schemas.common import Base
from regintel.schemas.common import ControlNature as N
from regintel.schemas.common import ControlType as T
from regintel.schemas.common import Frequency as F

KYC, DPDP, ITO = "RBI-KYC-MD", "DPDP-2023", "RBI-ITO-2023"
CCO, DPO = "Chief Compliance Officer", "Data Protection Officer"
CIO, CISO = "Chief Information Officer", "Chief Information Security Officer"
HVM = "Head of Vendor Management"


class RegLink(Base):
    regulation_id: str
    hint: str  # resolved to concrete obligation IDs in Phase 3


class ControlSpec(Base):
    id: str
    clause_no: str
    name: str
    text: str
    control_type: T
    nature: N
    frequency: F | None = None
    owner_role: str
    systems: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # "csv:<n>" | "att:<n>"; empty -> auto attestation
    maps_to: list[RegLink] = Field(default_factory=list)
    last_reviewed: date | None = None


class Narrative(Base):
    clause_no: str
    text: str


class SectionSpec(Base):
    no: str
    title: str
    narrative: list[Narrative] = Field(default_factory=list)
    controls: list[ControlSpec] = Field(default_factory=list)


class AnnexureSpec(Base):
    title: str
    paragraphs: list[str]


class PolicySpec(Base):
    id: str
    title: str
    owner_role: str
    version: str
    approved_by: str
    approved_on: date
    last_reviewed: date
    review_frequency: F = F.ANNUAL
    topics: list[str]
    sections: list[SectionSpec]
    annexures: list[AnnexureSpec] = Field(default_factory=list)

    def controls(self) -> list[ControlSpec]:
        return [c for s in self.sections for c in s.controls]


def L(reg: str, hint: str) -> RegLink:
    return RegLink(regulation_id=reg, hint=hint)


def P(no: str, text: str) -> Narrative:
    return Narrative(clause_no=no, text=text)


C = ControlSpec


def effective_evidence(c: ControlSpec) -> list[str]:
    return c.evidence or [f"att:{c.id.lower()}"]


# ───────────────────────────── KYC / AML POLICY (14) ─────────────────────────────
KYC_POL = PolicySpec(
    id="KYC-POL", title="Know Your Customer (KYC) and Anti-Money Laundering Policy",
    owner_role=CCO, version="4.2", approved_by="Board of Directors",
    approved_on=date(2024, 4, 25), last_reviewed=date(2024, 4, 25),
    topics=["KYC", "AML_CFT", "RECORD_RETENTION"],
    sections=[
        SectionSpec(no="1", title="Purpose and Scope", narrative=[
            P("1.1", "This Policy sets out the Know Your Customer (KYC), Anti-Money Laundering (AML) "
                     "and Combating the Financing of Terrorism (CFT) standards of Bharat Unity Bank Ltd. (the Bank)."),
            P("1.2", "This Policy applies to all business units, branches and channels of the Bank, "
                     "including digital and video-based onboarding."),
        ]),
        SectionSpec(no="2", title="Definitions", narrative=[
            P("2.1", "Officially Valid Document (OVD) has the meaning assigned to it in the applicable "
                     "directions of the Reserve Bank of India."),
            P("2.2", "Principal Officer means the officer nominated by the Bank to furnish information "
                     "to the Financial Intelligence Unit - India (FIU-IND)."),
        ]),
        SectionSpec(no="3", title="Governance", controls=[
            C(id="KYC-C01", clause_no="3.1", name="Board approval and annual review of KYC Policy",
              text="This Policy shall be approved by the Board of Directors and reviewed at least annually, "
                   "or earlier upon any material regulatory change.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=CCO,
              topics=["GOVERNANCE", "KYC"], maps_to=[L(KYC, "KYC policy duly approved by the Board")]),
            C(id="KYC-C02", clause_no="3.2", name="Designated Director and Principal Officer",
              text="The Bank shall nominate a Designated Director and a Principal Officer and communicate their "
                   "names, designation and contact details to FIU-IND, including upon any change.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN, owner_role=CCO,
              topics=["GOVERNANCE", "AML_CFT"],
              maps_to=[L(KYC, "nominate designated director and principal officer and communicate to FIU-IND")]),
        ]),
        SectionSpec(no="4", title="Customer Acceptance", controls=[
            C(id="KYC-C03", clause_no="4.1", name="No anonymous or fictitious accounts",
              text="No account shall be opened in an anonymous or fictitious name. The Core Banking System shall "
                   "block account activation unless the customer's OVD has been verified.",
              control_type=T.PREVENTIVE, nature=N.AUTOMATED, frequency=F.CONTINUOUS,
              owner_role="Head of Retail Operations", systems=["CBS"], topics=["KYC"],
              evidence=["csv:cdd_onboarding_log"],
              maps_to=[L(KYC, "no account opened in anonymous or fictitious or benami name")]),
            C(id="KYC-C04", clause_no="4.2", name="V-CIP conduct standards",
              text="Video-based Customer Identification Process (V-CIP) sessions shall be conducted only by trained "
                   "officials of the Bank, recorded end-to-end, geo-tagged and subjected to a liveness check "
                   "before the account is activated.",
              control_type=T.PREVENTIVE, nature=N.HYBRID, frequency=F.EVENT_DRIVEN,
              owner_role="Head of Digital Banking", systems=["V-CIP platform (VeriFast KYC)"],
              topics=["KYC"], evidence=["csv:vcip_sessions"],
              maps_to=[L(KYC, "V-CIP by trained official with recording, geo-tagging and liveness check")]),
        ]),
        SectionSpec(no="5", title="Customer Identification", controls=[
            C(id="KYC-C05", clause_no="5.1", name="Identity verification at onboarding",
              text="At onboarding, the identity and address of every individual customer shall be verified using "
                   "an OVD or Aadhaar-based e-KYC authentication, and the outcome shall be recorded in the "
                   "onboarding system.",
              control_type=T.PREVENTIVE, nature=N.HYBRID, frequency=F.EVENT_DRIVEN,
              owner_role="Head of Retail Operations", systems=["Onboarding system", "CBS"],
              topics=["KYC"], evidence=["csv:cdd_onboarding_log"],
              maps_to=[L(KYC, "customer due diligence using OVD or Aadhaar e-KYC authentication")]),
            C(id="KYC-C06", clause_no="5.2", name="Beneficial owner identification",
              text="For customers that are legal entities, the Bank shall identify the beneficial owner(s) as per "
                   "the thresholds prescribed under the PML Rules and take reasonable measures to verify their "
                   "identity. The Relationship Manager shall record a beneficial ownership attestation for each "
                   "such customer.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN,
              owner_role="Head of SME Banking", topics=["KYC"], evidence=["att:bo_attestations"],
              maps_to=[L(KYC, "identify beneficial owner of legal entity customers and verify identity")]),
            C(id="KYC-C07", clause_no="5.3", name="Sanctions screening before activation",
              text="Before any account is activated, the customer's name shall be screened against the UNSC "
                   "sanctions lists and the lists of individuals and entities designated under the Unlawful "
                   "Activities (Prevention) Act, 1967. Activation shall not proceed until screening is cleared.",
              control_type=T.PREVENTIVE, nature=N.AUTOMATED, frequency=F.EVENT_DRIVEN,
              owner_role="Head of AML Operations", systems=["Sanctions screening engine", "CBS"],
              topics=["KYC", "AML_CFT"], evidence=["csv:sanctions_screening_log"],
              maps_to=[L(KYC, "verify customer is not in UNSC sanctions lists or UAPA designated lists before opening account")]),
        ]),
        SectionSpec(no="6", title="Risk Categorisation and Ongoing Due Diligence", controls=[
            C(id="KYC-C08", clause_no="6.1", name="Customer risk categorisation",
              text="Every customer shall be assigned a risk category of Low, Medium or High at onboarding based on "
                   "customer profile, geography, product and channel, and the category shall be reviewed upon any "
                   "trigger event.",
              control_type=T.PREVENTIVE, nature=N.AUTOMATED, frequency=F.EVENT_DRIVEN, owner_role=CCO,
              systems=["Customer risk rating engine"], topics=["KYC"], evidence=["csv:cdd_onboarding_log"],
              maps_to=[L(KYC, "risk categorisation of customers as low, medium or high risk")]),
            C(id="KYC-C09", clause_no="6.2", name="Periodic KYC updation - High risk",
              text="Periodic updation of KYC shall be carried out at least once every two years for High risk customers.",
              control_type=T.DETECTIVE, nature=N.HYBRID, frequency=F.BIENNIAL,
              owner_role="Head of KYC Operations", systems=["CBS", "CRM"], topics=["KYC"],
              evidence=["csv:kyc_periodic_updation"],
              maps_to=[L(KYC, "periodic updation for high risk customers at least once every two years")]),
            C(id="KYC-C10", clause_no="6.3", name="Periodic KYC updation - Medium risk",
              text="Periodic updation of KYC shall be carried out at least once every ten years for Medium risk customers.",
              control_type=T.DETECTIVE, nature=N.HYBRID, owner_role="Head of KYC Operations",
              systems=["CBS", "CRM"], topics=["KYC"], evidence=["csv:kyc_periodic_updation"],
              maps_to=[L(KYC, "periodic updation for medium risk customers at least once every eight years")]),
            C(id="KYC-C11", clause_no="6.4", name="Periodic KYC updation - Low risk",
              text="Periodic updation of KYC shall be carried out at least once every ten years for Low risk customers.",
              control_type=T.DETECTIVE, nature=N.HYBRID, owner_role="Head of KYC Operations",
              systems=["CBS", "CRM"], topics=["KYC"], evidence=["csv:kyc_periodic_updation"],
              maps_to=[L(KYC, "periodic updation for low risk customers at least once every ten years")]),
            C(id="KYC-C12", clause_no="6.5", name="Alert review and STR filing",
              text="Transaction monitoring alerts shall be reviewed by AML Operations, and where a transaction is "
                   "determined to be suspicious, a Suspicious Transaction Report (STR) shall be furnished to "
                   "FIU-IND within seven working days of such determination.",
              control_type=T.DETECTIVE, nature=N.HYBRID, frequency=F.EVENT_DRIVEN,
              owner_role="Principal Officer", systems=["AML transaction monitoring system"],
              topics=["AML_CFT"], evidence=["csv:str_filing_log"],
              maps_to=[L(KYC, "report suspicious transactions to FIU-IND")]),
        ]),
        SectionSpec(no="7", title="Reporting and Record Keeping", controls=[
            C(id="KYC-C13", clause_no="7.1", name="Cash Transaction Reports",
              text="Cash Transaction Reports (CTR) shall be furnished to FIU-IND for every month by the 15th day "
                   "of the succeeding month.",
              control_type=T.DETECTIVE, nature=N.AUTOMATED, frequency=F.MONTHLY,
              owner_role="Principal Officer", topics=["AML_CFT"],
              maps_to=[L(KYC, "furnish cash transaction reports to FIU-IND")]),
            C(id="KYC-C14", clause_no="7.2", name="Record preservation - five years",
              text="Records of all transactions and of customer identification, including KYC documents and "
                   "account files, shall be preserved for at least five years after the business relationship "
                   "has ended or the account has been closed, whichever is later.",
              control_type=T.PREVENTIVE, nature=N.HYBRID, frequency=F.CONTINUOUS,
              owner_role="Head of Operations", systems=["CBS", "Document Management System"],
              topics=["RECORD_RETENTION", "KYC"],
              maps_to=[L(KYC, "preserve records of transactions and identification for at least five years after business relationship ended")]),
        ]),
    ],
)

# ───────────────────── DATA PROTECTION & RETENTION POLICY (10) ─────────────────────
DPR_POL = PolicySpec(
    id="DPR-POL", title="Personal Data Protection and Retention Policy",
    owner_role=DPO, version="2.0", approved_by="Board Risk Management Committee",
    approved_on=date(2024, 9, 30), last_reviewed=date(2024, 9, 30),
    topics=["DATA_PROTECTION", "CONSENT", "RECORD_RETENTION"],
    sections=[
        SectionSpec(no="1", title="Purpose and Scope", narrative=[
            P("1.1", "This Policy governs the processing of digital personal data by the Bank in its capacity "
                     "as a Data Fiduciary under the Digital Personal Data Protection Act, 2023."),
        ]),
        SectionSpec(no="2", title="Governance", controls=[
            C(id="DPR-C01", clause_no="2.1", name="Data Protection Officer",
              text="The Bank shall designate a Data Protection Officer based in India who shall be the point of "
                   "contact for grievance redressal, and shall publish the business contact details of the Data "
                   "Protection Officer on its website and mobile application.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN, owner_role=DPO,
              topics=["DATA_PROTECTION", "GRIEVANCE", "GOVERNANCE"],
              maps_to=[L(DPDP, "publish business contact information of person answering data principal questions"),
                       L(DPDP, "significant data fiduciary appoint data protection officer based in India")]),
            C(id="DPR-C02", clause_no="2.2", name="DPIA and independent data audit",
              text="The Bank shall conduct an annual Data Protection Impact Assessment and an independent data "
                   "audit, and report the outcome to the Board Risk Management Committee.",
              control_type=T.DETECTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=DPO,
              topics=["DATA_PROTECTION", "AUDIT"],
              maps_to=[L(DPDP, "significant data fiduciary periodic data protection impact assessment and independent data audit")]),
        ]),
        SectionSpec(no="3", title="Notice and Consent", controls=[
            C(id="DPR-C03", clause_no="3.1", name="Privacy notice at collection",
              text="Every request for consent shall be accompanied or preceded by a notice describing the personal "
                   "data to be processed and the purpose of processing, available in English or any language "
                   "specified in the Eighth Schedule to the Constitution, at the customer's option.",
              control_type=T.PREVENTIVE, nature=N.AUTOMATED, frequency=F.EVENT_DRIVEN,
              owner_role="Head of Digital Banking", systems=["Onboarding system", "Mobile app"],
              topics=["CONSENT", "DATA_PROTECTION"], evidence=["csv:consent_records"],
              maps_to=[L(DPDP, "notice describing personal data and purpose of processing"),
                       L(DPDP, "notice in English or a language in the Eighth Schedule")]),
            C(id="DPR-C04", clause_no="3.2", name="Consent capture and logging",
              text="Consent shall be obtained through a clear affirmative action, shall be specific to each purpose, "
                   "and shall be logged with timestamp, purpose code and notice version.",
              control_type=T.PREVENTIVE, nature=N.AUTOMATED, frequency=F.EVENT_DRIVEN,
              owner_role="Head of Digital Banking", systems=["Consent management module"],
              topics=["CONSENT"], evidence=["csv:consent_records"],
              maps_to=[L(DPDP, "consent free, specific, informed, unconditional and unambiguous with clear affirmative action")]),
        ]),
        SectionSpec(no="4", title="Purpose Limitation", controls=[
            C(id="DPR-C05", clause_no="4.1", name="Purpose limitation and data inventory",
              text="Personal data shall be processed only for the purpose for which consent was given or for "
                   "legitimate uses permitted by law, and the Bank shall maintain an inventory of personal data "
                   "categories, purposes and systems.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=DPO,
              topics=["DATA_PROTECTION"],
              maps_to=[L(DPDP, "process personal data only for lawful purpose with consent or for legitimate uses")]),
            C(id="DPR-C06", clause_no="4.2", name="Children's personal data",
              text="Before processing personal data of a child, the Bank shall obtain verifiable consent of the "
                   "parent or lawful guardian and shall not undertake tracking, behavioural monitoring or targeted "
                   "advertising directed at children.",
              control_type=T.PREVENTIVE, nature=N.HYBRID, frequency=F.EVENT_DRIVEN,
              owner_role="Head of Retail Banking", topics=["DATA_PROTECTION", "CONSENT"],
              maps_to=[L(DPDP, "verifiable consent of parent before processing personal data of a child"),
                       L(DPDP, "no tracking, behavioural monitoring or targeted advertising directed at children")]),
        ]),
        SectionSpec(no="5", title="Security Safeguards", controls=[
            C(id="DPR-C07", clause_no="5.1", name="Reasonable security safeguards",
              text="The Bank shall implement reasonable security safeguards, including encryption, masking and "
                   "access control, to prevent personal data breach.",
              control_type=T.PREVENTIVE, nature=N.HYBRID, frequency=F.CONTINUOUS, owner_role=CISO,
              topics=["DATA_PROTECTION", "CYBER_SECURITY"],
              maps_to=[L(DPDP, "reasonable security safeguards to prevent personal data breach")]),
        ]),
        SectionSpec(no="6", title="Retention and Erasure", controls=[
            C(id="DPR-C08", clause_no="6.1", name="Retention schedule",
              text="Retention periods for each category of personal data shall be specified in the Retention "
                   "Schedule at Annexure A, which shall be reviewed annually.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=DPO,
              topics=["RECORD_RETENTION", "DATA_PROTECTION"], last_reviewed=date(2021, 8, 16),
              maps_to=[L(DPDP, "erase personal data when purpose no longer served unless retention necessary for compliance with law")]),
            C(id="DPR-C09", clause_no="6.2", name="Erasure after account closure",
              text="Personal data of customers whose accounts have been closed, including KYC documents, shall be "
                   "erased from all systems within 180 days of account closure.",
              control_type=T.CORRECTIVE, nature=N.AUTOMATED, frequency=F.EVENT_DRIVEN,
              owner_role="Head of Operations", systems=["CBS", "Document Management System", "CRM"],
              topics=["RECORD_RETENTION", "DATA_PROTECTION"], evidence=["csv:data_erasure_log"],
              maps_to=[L(DPDP, "erase personal data when purpose no longer served unless retention necessary for compliance with law")]),
        ]),
        SectionSpec(no="7", title="Rights of Data Principals", controls=[
            C(id="DPR-C10", clause_no="7.1", name="Data principal requests",
              text="Requests from Data Principals for access to, correction, completion or updating of their "
                   "personal data shall be resolved within 30 days of receipt.",
              control_type=T.CORRECTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN, owner_role=DPO,
              topics=["DATA_PROTECTION", "GRIEVANCE"],
              maps_to=[L(DPDP, "right to obtain information about personal data processed"),
                       L(DPDP, "right to correction, completion, updating and erasure of personal data")]),
        ]),
    ],
    annexures=[AnnexureSpec(title="Annexure A: Retention Schedule", paragraphs=[
        "This Schedule was last reviewed in August 2021 and is aligned with the Information Technology "
        "(Reasonable Security Practices and Procedures and Sensitive Personal Data or Information) Rules, 2011.",
        "Customer identification and KYC documents: 180 days after account closure.",
        "Account and transaction data: 180 days after account closure, except where retained in books of account.",
        "CCTV footage at branches: 90 days.",
    ])],
)

# ─────────────────────────── IT OUTSOURCING POLICY (9) ───────────────────────────
ITO_POL = PolicySpec(
    id="ITO-POL", title="IT Outsourcing and Third-Party Risk Policy",
    owner_role=CIO, version="3.1", approved_by="Board of Directors",
    approved_on=date(2024, 3, 28), last_reviewed=date(2024, 3, 28),
    topics=["IT_OUTSOURCING", "VENDOR_RISK", "BCP_DR"],
    sections=[
        SectionSpec(no="1", title="Purpose and Scope", narrative=[
            P("1.1", "This Policy governs all arrangements under which the Bank uses third-party service "
                     "providers for information technology services, including cloud services."),
        ]),
        SectionSpec(no="2", title="Governance", controls=[
            C(id="ITO-C01", clause_no="2.1", name="Board policy and IT Strategy Committee oversight",
              text="The Board shall approve this Policy, and the IT Strategy Committee shall oversee all IT "
                   "outsourcing arrangements and review this Policy at least annually.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=CIO,
              topics=["IT_OUTSOURCING", "GOVERNANCE"],
              maps_to=[L(ITO, "board approved policy on IT outsourcing"),
                       L(ITO, "IT strategy committee oversight of outsourcing arrangements")]),
        ]),
        SectionSpec(no="3", title="Onboarding of Service Providers", controls=[
            C(id="ITO-C02", clause_no="3.1", name="Materiality assessment and register",
              text="Each IT outsourcing arrangement shall be assessed for materiality before onboarding, and all "
                   "arrangements shall be recorded in the Outsourcing Register with their materiality classification.",
              control_type=T.DETECTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN, owner_role=HVM,
              topics=["IT_OUTSOURCING", "VENDOR_RISK"], evidence=["csv:vendor_register"],
              maps_to=[L(ITO, "assess materiality of outsourcing arrangement"),
                       L(ITO, "maintain inventory of outsourced IT services")]),
            C(id="ITO-C03", clause_no="3.2", name="Service provider due diligence",
              text="Before entering into an arrangement, the Bank shall conduct due diligence on the service "
                   "provider covering financial soundness, technical capability, reputation and information "
                   "security posture.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN, owner_role=HVM,
              topics=["VENDOR_RISK"], evidence=["csv:vendor_register"],
              maps_to=[L(ITO, "due diligence of service provider before outsourcing")]),
        ]),
        SectionSpec(no="4", title="Contractual Requirements", controls=[
            C(id="ITO-C04", clause_no="4.1", name="Audit and access rights in contracts",
              text="Every outsourcing agreement shall provide for the right of the Bank and the Reserve Bank of "
                   "India, or persons authorised by them, to access and audit the service provider, together with "
                   "clauses on confidentiality, sub-contracting and termination.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN,
              owner_role="Head of Legal", topics=["IT_OUTSOURCING"],
              maps_to=[L(ITO, "agreement provides access and audit rights to regulated entity and RBI"),
                       L(ITO, "agreement covers confidentiality, sub-contracting and termination")]),
        ]),
        SectionSpec(no="5", title="Ongoing Monitoring", controls=[
            C(id="ITO-C05", clause_no="5.1", name="Annual risk assessment of material providers",
              text="Material service providers shall be subjected to a risk assessment at least annually, and the "
                   "results shall be reported to the IT Strategy Committee.",
              control_type=T.DETECTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=HVM,
              topics=["VENDOR_RISK"], evidence=["csv:vendor_register"],
              maps_to=[L(ITO, "periodic risk assessment and performance monitoring of service providers")]),
            C(id="ITO-C06", clause_no="5.2", name="Quarterly review of service-provider privileged access",
              text="Privileged access granted to service-provider personnel on Bank systems shall be reviewed every "
                   "quarter, and access that is no longer required shall be revoked.",
              control_type=T.DETECTIVE, nature=N.MANUAL, frequency=F.QUARTERLY, owner_role=CISO,
              systems=["PAM tool"], topics=["VENDOR_RISK", "CYBER_SECURITY"],
              evidence=["csv:vendor_privileged_access_review"],
              maps_to=[L(ITO, "controls over service provider access to regulated entity systems and data")]),
            C(id="ITO-C07", clause_no="5.3", name="Concentration risk monitoring",
              text="Concentration risk arising from reliance on a single service provider shall be monitored and "
                   "reported to the IT Strategy Committee every six months.",
              control_type=T.DETECTIVE, nature=N.MANUAL, frequency=F.SEMI_ANNUAL, owner_role=CIO,
              topics=["VENDOR_RISK"], maps_to=[L(ITO, "monitor concentration risk of service providers")]),
        ]),
        SectionSpec(no="6", title="Business Continuity and Exit", controls=[
            C(id="ITO-C08", clause_no="6.1", name="Joint DR drill with material providers",
              text="For each material outsourcing arrangement, the Bank shall maintain a Business Continuity and "
                   "Disaster Recovery plan with the service provider and conduct a joint disaster recovery drill "
                   "at least annually.",
              control_type=T.CORRECTIVE, nature=N.MANUAL, frequency=F.ANNUAL,
              owner_role="Head of IT Infrastructure", topics=["BCP_DR", "IT_OUTSOURCING"],
              evidence=["att:dr_drill_report"],
              maps_to=[L(ITO, "business continuity and disaster recovery plan with service provider periodically tested")]),
            C(id="ITO-C09", clause_no="6.2", name="Exit strategy",
              text="A documented exit strategy, including transition arrangements, shall be maintained for every "
                   "material outsourcing arrangement.",
              control_type=T.CORRECTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=HVM,
              topics=["IT_OUTSOURCING", "VENDOR_RISK"],
              maps_to=[L(ITO, "exit strategy for outsourcing arrangements")]),
        ]),
    ],
)

# ──────────────── INFORMATION SECURITY & INCIDENT POLICY (9) ────────────────
ISB_POL = PolicySpec(
    id="ISB-POL", title="Information Security and Cyber Incident Management Policy",
    owner_role=CISO, version="5.0", approved_by="Board of Directors",
    approved_on=date(2024, 11, 20), last_reviewed=date(2024, 11, 20),
    topics=["CYBER_SECURITY", "DATA_BREACH"],
    sections=[
        SectionSpec(no="1", title="Purpose and Scope", narrative=[
            P("1.1", "This Policy establishes the information security and cyber incident management "
                     "framework of the Bank for all information assets, including those operated by service providers."),
        ]),
        SectionSpec(no="2", title="Governance", controls=[
            C(id="ISB-C01", clause_no="2.1", name="Board policy and CISO",
              text="The Board shall approve this Policy and the Bank shall appoint a Chief Information Security "
                   "Officer reporting independently of the IT function.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role=CISO,
              topics=["CYBER_SECURITY", "GOVERNANCE"]),
        ]),
        SectionSpec(no="3", title="Access Management", controls=[
            C(id="ISB-C02", clause_no="3.1", name="Role-based access and leaver revocation",
              text="Access to information systems shall be granted on a role basis, and access of leavers shall be "
                   "revoked within one working day of exit.",
              control_type=T.PREVENTIVE, nature=N.HYBRID, frequency=F.EVENT_DRIVEN,
              owner_role="Head of IT Operations", systems=["IAM"], topics=["CYBER_SECURITY"]),
            C(id="ISB-C03", clause_no="3.2", name="Quarterly review of internal privileged accounts",
              text="All internal privileged accounts shall be reviewed every quarter by the system owner.",
              control_type=T.DETECTIVE, nature=N.MANUAL, frequency=F.QUARTERLY, owner_role="System Owners",
              systems=["PAM tool"], topics=["CYBER_SECURITY"], evidence=["csv:access_reviews"]),
            C(id="ISB-C04", clause_no="3.3", name="Multi-factor authentication",
              text="Multi-factor authentication shall be enforced for all privileged and remote access.",
              control_type=T.PREVENTIVE, nature=N.AUTOMATED, frequency=F.CONTINUOUS,
              owner_role="Head of IT Operations", topics=["CYBER_SECURITY"]),
        ]),
        SectionSpec(no="4", title="Technical Controls", controls=[
            C(id="ISB-C05", clause_no="4.1", name="Vulnerability management",
              text="Vulnerability assessments shall be conducted every quarter, and critical vulnerabilities shall "
                   "be remediated within 15 days of identification.",
              control_type=T.DETECTIVE, nature=N.HYBRID, frequency=F.QUARTERLY, owner_role=CISO,
              topics=["CYBER_SECURITY"]),
            C(id="ISB-C06", clause_no="4.2", name="Log retention",
              text="Logs of all ICT systems shall be maintained securely within Indian jurisdiction for a rolling "
                   "period of 180 days.",
              control_type=T.DETECTIVE, nature=N.AUTOMATED, frequency=F.CONTINUOUS,
              owner_role="Head of IT Operations", systems=["SIEM"], topics=["CYBER_SECURITY"]),
            C(id="ISB-C07", clause_no="4.3", name="Encryption standards",
              text="Data at rest shall be encrypted using AES-256 or stronger and data in transit shall be "
                   "protected using TLS 1.2 or higher.",
              control_type=T.PREVENTIVE, nature=N.AUTOMATED, frequency=F.CONTINUOUS, owner_role=CISO,
              topics=["CYBER_SECURITY", "DATA_PROTECTION"],
              maps_to=[L(DPDP, "reasonable security safeguards to prevent personal data breach")]),
        ]),
        SectionSpec(no="5", title="Incident Management", controls=[
            C(id="ISB-C08", clause_no="5.1", name="Cyber incident reporting",
              text="Cyber security incidents shall be reported to CERT-In within six hours of being noticed and to "
                   "the Reserve Bank of India within the timelines prescribed by it.",
              control_type=T.CORRECTIVE, nature=N.MANUAL, frequency=F.EVENT_DRIVEN, owner_role=CISO,
              systems=["SIEM", "Incident management tool"], topics=["DATA_BREACH", "CYBER_SECURITY"],
              evidence=["csv:security_incidents"],
              maps_to=[L(DPDP, "intimate personal data breach to the Data Protection Board and each affected data principal")]),
        ]),
        SectionSpec(no="6", title="Awareness", controls=[
            C(id="ISB-C09", clause_no="6.1", name="Security awareness training",
              text="All employees shall complete information security awareness training annually.",
              control_type=T.PREVENTIVE, nature=N.MANUAL, frequency=F.ANNUAL, owner_role="Head of HR",
              topics=["CYBER_SECURITY"]),
        ]),
    ],
)

POLICIES: list[PolicySpec] = [KYC_POL, DPR_POL, ITO_POL, ISB_POL]


def all_controls() -> list[ControlSpec]:
    return [c for p in POLICIES for c in p.controls()]