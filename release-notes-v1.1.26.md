# Ground Data Processing Tool 1.1.26

Changes from 1.1.25:

- Fixed Publish counting already-published records as new when their CHECKER or DATA USABILITY changed. The duplicate check now uses AREA INDEX + DATE + LO + negotiator/sourcer name only. CHECKER and DATA USABILITY are worked out again on every Generate run, so they no longer make the same report look new and get published twice. With Override on, the existing record is updated with the new CHECKER / DATA USABILITY instead.
