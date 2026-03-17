import { Types } from '@ohif/core';

const genAiMprProtocol: Types.HangingProtocol.Protocol = {
  id: 'genAiMpr',
  description: '3-view MPR layout for CT studies in Generative AI mode',
  name: 'GenAI CT MPR',
  protocolMatchingRules: [
    {
      id: 'CTStudy',
      weight: 150,
      attribute: 'ModalitiesInStudy',
      constraint: {
        contains: 'CT',
      },
      required: true,
    },
    {
      id: 'HasImages',
      weight: 25,
      attribute: 'numberOfDisplaySetsWithImages',
      constraint: {
        greaterThan: 0,
      },
      required: true,
    },
  ],
  toolGroupIds: ['mpr'],
  displaySetSelectors: {
    ctDisplaySet: {
      seriesMatchingRules: [
        {
          attribute: 'isReconstructable',
          constraint: {
            equals: {
              value: true,
            },
          },
          required: true,
        },
        {
          attribute: 'Modality',
          constraint: {
            equals: {
              value: 'CT',
            },
          },
          required: true,
        },
      ],
    },
  },
  defaultViewport: {
    viewportOptions: {
      viewportType: 'volume',
      toolGroupId: 'mpr',
      orientation: 'axial',
      allowUnmatchedView: true,
      initialImageOptions: {
        preset: 'middle',
      },
    },
    displaySets: [
      {
        id: 'ctDisplaySet',
        matchedDisplaySetsIndex: -1,
      },
    ],
  },
  stages: [
    {
      id: 'mpr-1x3',
      name: 'MPR 1x3',
      requiredViewports: 3,
      preferredViewports: 3,
      stageActivation: {
        enabled: {
          minViewportsMatched: 1,
        },
      },
      viewportStructure: {
        layoutType: 'grid',
        properties: {
          rows: 1,
          columns: 3,
          layoutOptions: [
            {
              x: 0,
              y: 0,
              width: 1 / 3,
              height: 1,
            },
            {
              x: 1 / 3,
              y: 0,
              width: 1 / 3,
              height: 1,
            },
            {
              x: 2 / 3,
              y: 0,
              width: 1 / 3,
              height: 1,
            },
          ],
        },
      },
      viewports: [
        {
          viewportOptions: {
            viewportId: 'genai-mpr-axial',
            toolGroupId: 'mpr',
            viewportType: 'volume',
            orientation: 'axial',
            allowUnmatchedView: true,
            initialImageOptions: {
              preset: 'middle',
            },
            syncGroups: [
              {
                type: 'voi',
                id: 'mpr',
                source: true,
                target: true,
                options: {
                  syncColormap: true,
                },
              },
            ],
          },
          displaySets: [{ id: 'ctDisplaySet' }],
        },
        {
          viewportOptions: {
            viewportId: 'genai-mpr-sagittal',
            toolGroupId: 'mpr',
            viewportType: 'volume',
            orientation: 'sagittal',
            allowUnmatchedView: true,
            initialImageOptions: {
              preset: 'middle',
            },
            syncGroups: [
              {
                type: 'voi',
                id: 'mpr',
                source: true,
                target: true,
                options: {
                  syncColormap: true,
                },
              },
            ],
          },
          displaySets: [{ id: 'ctDisplaySet' }],
        },
        {
          viewportOptions: {
            viewportId: 'genai-mpr-coronal',
            toolGroupId: 'mpr',
            viewportType: 'volume',
            orientation: 'coronal',
            allowUnmatchedView: true,
            initialImageOptions: {
              preset: 'middle',
            },
            syncGroups: [
              {
                type: 'voi',
                id: 'mpr',
                source: true,
                target: true,
                options: {
                  syncColormap: true,
                },
              },
            ],
          },
          displaySets: [{ id: 'ctDisplaySet' }],
        },
      ],
    },
  ],
  numberOfPriorsReferenced: -1,
};

export default genAiMprProtocol;
