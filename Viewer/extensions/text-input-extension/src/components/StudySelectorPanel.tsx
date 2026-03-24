import React, { useEffect, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import requestDisplaySetCreationForStudy from '@ohif/extension-default/src/Panels/requestDisplaySetCreationForStudy';

const LOCAL_GROUPS_STORAGE_KEY = 'studyGroupByUID';
const GENERATIVE_AI_PLACEHOLDER_STUDY_UID =
  '1.2.826.0.1.3680043.8.498.92334923612841918328708913924036869452';

function normalizeStudies(studies = []) {
  return studies
    .map(study => ({
      studyInstanceUid: study.studyInstanceUid || study.StudyInstanceUID,
      description: study.description || study.StudyDescription || '',
      modalities: study.modalities || study.ModalitiesInStudy || '',
      date: study.date || study.StudyDate || '',
      time: study.time || study.StudyTime || '',
      patientName: study.patientName || study.PatientName || '',
    }))
    .filter(study => study.studyInstanceUid && study.studyInstanceUid !== GENERATIVE_AI_PLACEHOLDER_STUDY_UID);
}

function getLocalStudyGroup(studyInstanceUid) {
  if (!studyInstanceUid) {
    return 'None';
  }

  try {
    const raw = localStorage.getItem(LOCAL_GROUPS_STORAGE_KEY);
    if (!raw) {
      return 'None';
    }
    const map = JSON.parse(raw);
    const value = map?.[studyInstanceUid];
    return value === 'A' || value === 'B' ? value : 'None';
  } catch (error) {
    return 'None';
  }
}

function getStudyGroup(studyInstanceUid, importedGroupMap = {}) {
  const localGroup = getLocalStudyGroup(studyInstanceUid);
  if (localGroup === 'A' || localGroup === 'B') {
    return localGroup;
  }

  const importedGroup = importedGroupMap?.[studyInstanceUid];
  return importedGroup === 'A' || importedGroup === 'B' ? importedGroup : 'None';
}

function formatStudyLabel(study) {
  const pieces = [];
  if (study.description) {
    pieces.push(study.description);
  }
  if (study.modalities) {
    pieces.push(study.modalities);
  }
  return pieces.join(' - ') || study.studyInstanceUid;
}

function StudySelectorPanel({ extensionManager, servicesManager }) {
  const dataSource = extensionManager.getDataSources()[0];
  const { displaySetService, uiNotificationService } = servicesManager.services;
  const [studies, setStudies] = useState([]);
  const [importedGroupMap, setImportedGroupMap] = useState({});
  const [searchText, setSearchText] = useState('');
  const [groupFilter, setGroupFilter] = useState('All');
  const [loadedStudyUIDs, setLoadedStudyUIDs] = useState([]);

  useEffect(() => {
    let cancelled = false;

    const loadStudies = async () => {
      const result = await dataSource.query.studies.search({});
      if (!cancelled) {
        setStudies(normalizeStudies(result));
      }
    };

    loadStudies().catch(() => {
      if (!cancelled) {
        setStudies([]);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [dataSource]);

  useEffect(() => {
    const syncLoadedStudies = () => {
      const activeDisplaySets = displaySetService.getActiveDisplaySets() || [];
      const studyUIDs = [
        ...new Set(
          activeDisplaySets
            .map(displaySet => displaySet?.StudyInstanceUID)
            .filter(Boolean)
        ),
      ];
      setLoadedStudyUIDs(studyUIDs);
    };

    syncLoadedStudies();

    const addedSubscription = displaySetService.subscribe(
      displaySetService.EVENTS.DISPLAY_SETS_ADDED,
      syncLoadedStudies
    );
    const changedSubscription = displaySetService.subscribe(
      displaySetService.EVENTS.DISPLAY_SETS_CHANGED,
      syncLoadedStudies
    );

    return () => {
      addedSubscription.unsubscribe();
      changedSubscription.unsubscribe();
    };
  }, [displaySetService]);

  useEffect(() => {
    let cancelled = false;

    const loadGroupMap = async () => {
      try {
        const response = await fetch('/study_groups.generated.json', { cache: 'no-store' });
        if (!response.ok) {
          return;
        }
        const data = await response.json();
        if (!cancelled && data && typeof data === 'object') {
          setImportedGroupMap(data);
        }
      } catch (error) {
        if (!cancelled) {
          setImportedGroupMap({});
        }
      }
    };

    loadGroupMap();

    return () => {
      cancelled = true;
    };
  }, []);

  const filteredStudies = useMemo(() => {
    const query = searchText.trim().toLowerCase();

    return studies
      .filter(study => {
        const group = getStudyGroup(study.studyInstanceUid, importedGroupMap);
        if (groupFilter !== 'All' && group !== groupFilter) {
          return false;
        }

        if (!query) {
          return true;
        }

        return [
          study.studyInstanceUid,
          study.description,
          study.patientName,
          study.modalities,
          group,
        ]
          .join(' ')
          .toLowerCase()
          .includes(query);
      })
      .sort((a, b) => {
        const aKey = `${a.date || ''}${a.time || ''}`;
        const bKey = `${b.date || ''}${b.time || ''}`;
        return bKey.localeCompare(aKey);
      });
  }, [studies, importedGroupMap, searchText, groupFilter]);

  const addStudyToViewer = async studyInstanceUid => {
    if (!studyInstanceUid) {
      return;
    }

    try {
      await requestDisplaySetCreationForStudy(
        dataSource,
        displaySetService,
        studyInstanceUid,
        true
      );
    } catch (error) {
      uiNotificationService.show({
        title: 'Load study',
        message: 'The selected study could not be loaded into the current viewer.',
        type: 'error',
        duration: 3000,
      });
    }
  };

  return (
    <div className="ohif-scrollbar invisible-scrollbar flex h-full flex-col overflow-y-auto p-2 text-white">
      <div className="mb-2 text-sm font-semibold">Study Browser</div>
      <input
        className="mb-2 rounded border border-secondary-dark bg-black p-2 text-sm text-white"
        placeholder="Search studies"
        value={searchText}
        onChange={event => setSearchText(event.target.value)}
      />
      <div className="mb-3 flex gap-2">
        {['All', 'A', 'B'].map(value => (
          <button
            key={value}
            type="button"
            className={`rounded px-2 py-1 text-xs ${
              groupFilter === value ? 'bg-primary-main text-black' : 'bg-secondary-dark text-white'
            }`}
            onClick={() => setGroupFilter(value)}
          >
            {value}
          </button>
        ))}
      </div>
      <div className="flex flex-col gap-2">
        {filteredStudies.map(study => {
          const loaded = loadedStudyUIDs.includes(study.studyInstanceUid);
          const group = getStudyGroup(study.studyInstanceUid, importedGroupMap);
          return (
            <button
              key={study.studyInstanceUid}
              type="button"
              className="rounded border border-secondary-dark bg-black p-2 text-left hover:border-primary-main"
              onClick={() => addStudyToViewer(study.studyInstanceUid)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold">{group !== 'None' ? `Group ${group}` : 'Ungrouped'}</span>
                <span className="text-[11px] text-secondary-light">{loaded ? 'Loaded' : 'Open'}</span>
              </div>
              <div className="mt-1 text-sm">{formatStudyLabel(study)}</div>
              <div className="mt-1 truncate text-[11px] text-secondary-light">{study.studyInstanceUid}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

StudySelectorPanel.propTypes = {
  extensionManager: PropTypes.object.isRequired,
  servicesManager: PropTypes.object.isRequired,
};

export default StudySelectorPanel;
