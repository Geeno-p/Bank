"""Planted gaps, contradictions and change-demo definition (the answer key's ground truth)."""
from typing import Literal

from regintel.schemas.common import Base, GapType
from regintel.schemas.conflict import RelationType, ResolutionPrinciple
from regintel.synthetic.spec import DPDP, ITO, KYC

WITHHELD_EVIDENCE = {"att:bo_attestations", "att:dr_drill_report"}


class PlantedGap(Base):
    id: str
    gap_type: GapType
    regulation_id: str
    obligation_hint: str
    control_id: str | None
    title: str
    detection_signal: str
    evidence_key: str | None = None


class Side(Base):
    kind: Literal["obligation", "control"]
    ref: str  # regulation_id or control_id
    hint: str


class PlantedContradiction(Base):
    id: str
    scope: Literal["REG_VS_REG", "POLICY_VS_POLICY"]
    side_a: Side
    side_b: Side
    expected_relation: RelationType
    acceptable_relations: list[RelationType]
    expected_resolution: ResolutionPrinciple
    harmonised_hint: str
    note: str = ""


class ChangeDemo(Base):
    control_id: str
    regulation_id: str
    parameter: str
    baseline_value: str
    hypothetical_value: str
    note: str


G = PlantedGap
PLANTED_GAPS: list[PlantedGap] = [
    G(id="G01", gap_type=GapType.DESIGN_GAP, regulation_id=KYC, control_id="KYC-C10",
      obligation_hint="periodic updation for medium risk customers at least once every eight years",
      title="Medium-risk periodic KYC set at 10 years; regulation requires 8",
      detection_signal="Parameter mismatch 10y vs 8y; 860 medium-risk customers last updated 8-10y ago "
                       "are CURRENT per policy but overdue per regulation",
      evidence_key="csv:kyc_periodic_updation"),
    G(id="G02", gap_type=GapType.UNMAPPED, regulation_id=KYC, control_id=None,
      obligation_hint="upload KYC records to the Central KYC Records Registry (CKYCR)",
      title="No control for CKYCR upload",
      detection_signal="No control in any policy maps to the CKYCR obligation"),
    G(id="G03", gap_type=GapType.MISSING_EVIDENCE, regulation_id=KYC, control_id="KYC-C06",
      obligation_hint="identify beneficial owner of legal entity customers and verify identity",
      title="Beneficial ownership attestations not available",
      detection_signal="Control expects att:bo_attestations; manifest has no evidence for KYC-C06",
      evidence_key="att:bo_attestations"),
    G(id="G04", gap_type=GapType.OPERATING_FAILURE, regulation_id=KYC, control_id="KYC-C07",
      obligation_hint="verify customer is not in sanctions lists before opening account",
      title="6% of Q4 accounts activated without prior sanctions screening",
      detection_signal="screened_before_activation == False in 180/3000 rows (6.0% > 5% tolerance)",
      evidence_key="csv:sanctions_screening_log"),
    G(id="G05", gap_type=GapType.PARTIAL_COVERAGE, regulation_id=DPDP, control_id="ISB-C08",
      obligation_hint="intimate personal data breach to the Data Protection Board and each affected data principal",
      title="Incident reporting covers CERT-In/RBI only; no DPB or data principal intimation",
      detection_signal="Mapping missing_elements: DPB intimation, data principal intimation; "
                       "personal-data incidents notified to 'CERT-In;RBI' only",
      evidence_key="csv:security_incidents"),
    G(id="G06", gap_type=GapType.UNMAPPED, regulation_id=DPDP, control_id=None,
      obligation_hint="right to withdraw consent with ease comparable to giving consent",
      title="No consent withdrawal mechanism",
      detection_signal="No control maps; consent_records has zero WITHDRAWN records",
      evidence_key="csv:consent_records"),
    G(id="G07", gap_type=GapType.STALE_CONTROL, regulation_id=DPDP, control_id="DPR-C08",
      obligation_hint="erase personal data when purpose no longer served unless retention required by law",
      title="Retention schedule last reviewed Aug 2021, aligned to SPDI Rules 2011",
      detection_signal="last_reviewed 2021-08-16 exceeds annual review cycle; references pre-DPDP framework",
      evidence_key="att:dpr-c08"),
    G(id="G08", gap_type=GapType.DESIGN_GAP, regulation_id=KYC, control_id="DPR-C09",
      obligation_hint="preserve records of identification for at least five years after relationship ended",
      title="KYC documents erased 180 days after closure, breaching 5-year retention",
      detection_signal="data_erasure_log: KYC_DOCUMENTS erased ~180 days after closure; conflicts with KYC-C14",
      evidence_key="csv:data_erasure_log"),
    G(id="G09", gap_type=GapType.MISSING_EVIDENCE, regulation_id=ITO, control_id="ITO-C08",
      obligation_hint="BCP/DR plan with service provider periodically tested",
      title="No evidence of annual joint DR drill with material providers",
      detection_signal="Control expects att:dr_drill_report; manifest has no evidence for ITO-C08",
      evidence_key="att:dr_drill_report"),
    G(id="G10", gap_type=GapType.OPERATING_FAILURE, regulation_id=ITO, control_id="ITO-C06",
      obligation_hint="controls over service provider access to systems and data",
      title="14% of service-provider privileged accounts not reviewed this quarter",
      detection_signal="reviewed_in_quarter == False in 17/120 (14.2%); 5 no longer required",
      evidence_key="csv:vendor_privileged_access_review"),
]

CONTRADICTIONS: list[PlantedContradiction] = [
    PlantedContradiction(
        id="X1", scope="REG_VS_REG",
        side_a=Side(kind="obligation", ref=KYC,
                    hint="preserve records for at least five years after business relationship ended"),
        side_b=Side(kind="obligation", ref=DPDP,
                    hint="erase personal data when purpose no longer served or consent withdrawn"),
        expected_relation=RelationType.CONFLICT,
        acceptable_relations=[RelationType.CONFLICT, RelationType.RELATED],
        expected_resolution=ResolutionPrinciple.SEQUENTIAL,
        harmonised_hint="Retain KYC/transaction records for the statutory period after relationship ends, "
                        "then erase; legal-hold override.",
        note="Apparent conflict: DPDP permits retention required by law. Verify wording in source texts."),
    PlantedContradiction(
        id="X2", scope="POLICY_VS_POLICY",
        side_a=Side(kind="control", ref="KYC-C14", hint="preserve KYC records five years after closure"),
        side_b=Side(kind="control", ref="DPR-C09", hint="erase KYC documents within 180 days of closure"),
        expected_relation=RelationType.CONFLICT,
        acceptable_relations=[RelationType.CONFLICT],
        expected_resolution=ResolutionPrinciple.STATUTORY_OVERRIDE,
        harmonised_hint="Amend DPR-C09: erase only after KYC/PMLA retention period, excluding legal holds."),
]

CHANGE_DEMO = ChangeDemo(
    control_id="KYC-C09", regulation_id=KYC, parameter="periodic_updation_high_risk",
    baseline_value="2 years", hypothetical_value="1 year",
    note="HYPOTHETICAL amendment for demo only; not an actual RBI change.")