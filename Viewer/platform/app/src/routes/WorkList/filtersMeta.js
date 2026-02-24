import i18n from 'i18next';

const filtersMeta = [
  {
    name: 'patientName',
    displayName: i18n.t('StudyList:PatientName'),
    inputType: 'Text',
    isSortable: true,
    gridCol: 4,
  },
  {
    name: 'mrn',
    displayName: i18n.t('StudyList:MRN'),
    inputType: 'Text',
    isSortable: true,
    gridCol: 3,
  },
  {
    name: 'studyDate',
    displayName: i18n.t('StudyList:StudyDate'),
    inputType: 'DateRange',
    isSortable: true,
    gridCol: 5,
  },
  {
    name: 'description',
    displayName: i18n.t('StudyList:Description'),
    inputType: 'Text',
    isSortable: true,
    gridCol: 4,
  },
  {
    name: 'modalities',
    displayName: i18n.t('StudyList:Modality'),
    inputType: 'MultiSelect',
    inputProps: {
      options: [
        { value: 'CT', label: 'CT' },
        { value: 'CR,DX', label: 'X-ray' },
      ],
    },
    isSortable: true,
    gridCol: 3,
  },
  {
    name: 'group',
    displayName: 'Group',
    inputType: 'MultiSelect',
    inputProps: {
      options: [
        { value: 'None', label: 'None' },
        { value: 'A', label: 'A' },
        { value: 'B', label: 'B' },
      ],
    },
    isSortable: true,
    gridCol: 3,
  },
  {
    name: 'instances',
    displayName: i18n.t('StudyList:Instances'),
    inputType: 'None',
    isSortable: false,
    gridCol: 2,
  },
];

export default filtersMeta;
