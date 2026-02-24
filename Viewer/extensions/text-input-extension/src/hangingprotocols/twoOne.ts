const twoOneProtocol: Types.HangingProtocol.Protocol = {
    /**
     * Forced single viewport for Generative AI mode.
     */
    id: 'twoOneGenAI',
    description: '1x1 grid layout',
    name: '1x1',
    protocolMatchingRules: [
      {
        id: 'OneOrMoreSeries',
        weight: 25,
        attribute: 'numberOfDisplaySetsWithImages',
        constraint: {
          greaterThan: 0,
        },
      },
    ],
    toolGroupIds: ['default'],
    displaySetSelectors: {
      defaultDisplaySetId: {
        seriesMatchingRules: [
          {
            attribute: 'numImageFrames',
            constraint: {
              greaterThan: { value: 0 },
            },
            required: true,
          },
          {
            attribute: 'isDisplaySetFromUrl',
            weight: 10,
            constraint: {
              equals: true,
            },
          },
        ],
      },
    },
    defaultViewport: {
      viewportOptions: {
        viewportType: 'stack',
        toolGroupId: 'default',
        allowUnmatchedView: true,
      },
      displaySets: [
        {
          id: 'defaultDisplaySetId',
          matchedDisplaySetsIndex: -1,
        },
      ],
    },
    stages: [
    // 1x1 stage (forced)
    {
      id: '1x1',
      requiredViewports: 1,
      preferredViewports: 1,
      stageActivation: {
        enabled: {
          minViewportsMatched: 1,
        },
      },
      viewportStructure: {
        layoutType: 'grid',
        properties: {
          rows: 1,
          columns: 1,
        },
      },
      viewports: [
        {
          viewportOptions: {
            toolGroupId: 'default',
            allowUnmatchedView: true,
          },
          displaySets: [
            {
              id: 'defaultDisplaySetId',
            },
          ],
        },
      ],
    },
    ],
    numberOfPriorsReferenced: -1,
  };

export default twoOneProtocol;
