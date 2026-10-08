"""Create a HYPOTHETICAL amended KYC version for the change demo (not a real RBI change)."""
from regintel.synthetic.regulations import write_all

AMEND = {
    "at least once in every two years for high risk customers":
        "at least once in every year for high risk customers",                     # TIGHTENED
    "The Reserve Bank may issue clarifications on these Directions from time to time.":
        "The Reserve Bank may, from time to time, issue clarifications on these Directions.",  # EDITORIAL
}
for p in write_all("v-amended", AMEND, only={"RBI-KYC-MD"}):
    print("wrote", p)