import React, { useCallback, useEffect, useRef, useState } from 'react';

function TextArea({ servicesManager, showPrompt = true }) {
  const { displaySetService, viewportGridService, cornerstoneViewportService } =
    servicesManager.services;

  const [orthancStudyID, setOrthancStudyID] = useState('');
  const [activeStudyInstanceUID, setActiveStudyInstanceUID] = useState('');
  const [activeSeriesInstanceUID, setActiveSeriesInstanceUID] = useState('');
  const [reportPromptData, setReportPromptData] = useState('');
  const [reportFindingsData, setReportFindingsData] = useState('');
  const [reportImpressionsData, setReportImpressionsData] = useState('');
  const [reportGroupData, setReportGroupData] = useState('None');
  const [importedGroupMap, setImportedGroupMap] = useState({});
  const [isImportedReadOnly, setIsImportedReadOnly] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [status, setStatus] = useState('');
  const loadRequestRef = useRef(0);

  const orthancAuth = `Basic ${window.btoa('orthanc:orthanc')}`;
  const METADATA_KEYS = {
    Prompt: ['Prompt', '1026'],
    Findings: ['Findings', '1024'],
    Impressions: ['Impressions', '1025'],
    Group: ['Group', '1027'],
  };
  const LOCAL_GROUPS_STORAGE_KEY = 'studyGroupByUID';
  const GENERATIVE_AI_PLACEHOLDER_STUDY_UID =
    '1.2.826.0.1.3680043.8.498.92334923612841918328708913924036869452';
  const allowImportedReportEditing = window?.config?.allowImportedReportEditing !== false;

  const getLocalStudyGroup = studyInstanceUID => {
    if (!studyInstanceUID) {
      return 'None';
    }
    try {
      const raw = localStorage.getItem(LOCAL_GROUPS_STORAGE_KEY);
      if (!raw) {
        return 'None';
      }
      const map = JSON.parse(raw);
      const value = map?.[studyInstanceUID];
      return value === 'A' || value === 'B' ? value : 'None';
    } catch (error) {
      return 'None';
    }
  };

  const setLocalStudyGroup = (studyInstanceUID, groupValue) => {
    if (!studyInstanceUID) {
      return;
    }
    try {
      const raw = localStorage.getItem(LOCAL_GROUPS_STORAGE_KEY);
      const map = raw ? JSON.parse(raw) : {};
      if (groupValue === 'A' || groupValue === 'B') {
        map[studyInstanceUID] = groupValue;
      } else {
        delete map[studyInstanceUID];
      }
      localStorage.setItem(LOCAL_GROUPS_STORAGE_KEY, JSON.stringify(map));
    } catch (error) {
      // Ignore storage errors (private mode/quota/etc.).
    }
  };

  const orthancFetch = async ({ path, method = 'GET', body = null, contentType = 'text/plain' }) => {
    const attempts = [
      { base: '/pacs', withAuth: true },
      { base: '/pacs', withAuth: false },
      { base: '', withAuth: true },
      { base: '', withAuth: false },
    ];

    let lastResponse = null;
    for (const attempt of attempts) {
      const headers: Record<string, string> = { 'Content-Type': contentType };
      if (attempt.withAuth) {
        headers.Authorization = orthancAuth;
      }

      try {
        const response = await fetch(`${attempt.base}${path}`, {
          method,
          headers,
          body,
        });
        lastResponse = response;

        if ([401, 403, 404].includes(response.status)) {
          continue;
        }
        return response;
      } catch (error) {
        // try next endpoint variant
      }
    }

    return lastResponse;
  };

  const getActiveDisplaySet = () => {
    const activeViewportId = viewportGridService.getActiveViewportId();
    const viewportState = viewportGridService.getState();
    const viewports = viewportState?.viewports;
    const activeViewport =
      typeof viewports?.get === 'function'
        ? viewports.get(activeViewportId)
        : viewports?.[activeViewportId];
    const activeDisplaySetUID = activeViewport?.displaySetInstanceUIDs?.[0];

    if (activeDisplaySetUID) {
      return displaySetService.getDisplaySetByUID(activeDisplaySetUID);
    }

    const activeDisplaySets = displaySetService.getActiveDisplaySets();
    return activeDisplaySets?.[0];
  };

  const getGroupFromDisplaySet = activeDisplaySet => {
    const firstImage = activeDisplaySet?.images?.[0];
    const rawCandidates = [
      firstImage?.ImageComments,
      firstImage?.imageComments,
      firstImage?.['00204000'],
      firstImage?.['x00204000'],
      activeDisplaySet?.instance?.ImageComments,
      activeDisplaySet?.instance?.imageComments,
      activeDisplaySet?.instance?.['00204000'],
      activeDisplaySet?.instance?.['x00204000'],
    ];

    for (const raw of rawCandidates) {
      if (typeof raw !== 'string') {
        continue;
      }
      const match = /^Group=(A|B)$/i.exec(raw.trim());
      if (match) {
        return match[1].toUpperCase();
      }
    }

    return '';
  };

  const isImportedSampleDisplaySet = activeDisplaySet => {
    const firstImage = activeDisplaySet?.images?.[0];
    const candidates = [
      activeDisplaySet?.Manufacturer,
      activeDisplaySet?.instance?.Manufacturer,
      firstImage?.Manufacturer,
      firstImage?.manufacturer,
      activeDisplaySet?.instance?.['00110010'],
      firstImage?.['00110010'],
    ];

    return candidates.some(value => {
      if (typeof value !== 'string') {
        return false;
      }
      const normalized = value.trim();
      return normalized === 'import_to_orthanc.py' || normalized === 'GenAIForEducation';
    });
  };
  const getStudyUIDFromUrl = () => {
    try {
      const currentUrl = new URL(window.location.href);
      const studyUIDs = currentUrl.searchParams.getAll('StudyInstanceUIDs');
      return studyUIDs?.[0] || '';
    } catch (error) {
      return '';
    }
  };

  const getOrthancStudyID = async studyInstanceUID => {
    if (!studyInstanceUID) {
      return null;
    }

    try {
      const lookupResponse = await orthancFetch({
        path: '/tools/lookup',
        method: 'POST',
        body: studyInstanceUID,
      });

      if (lookupResponse?.ok) {
        const lookupData = await lookupResponse.json();
        const studyMatch = Array.isArray(lookupData)
          ? lookupData.find(item => String(item?.Type || '').toLowerCase() === 'study')
          : null;
        if (studyMatch?.ID) {
          return studyMatch.ID;
        }
      }

      const findResponse = await orthancFetch({
        path: '/tools/find',
        method: 'POST',
        contentType: 'application/json',
        body: JSON.stringify({
          Level: 'Study',
          Expand: true,
          Query: { StudyInstanceUID: studyInstanceUID },
        }),
      });

      if (!findResponse?.ok) {
        return null;
      }

      const findData = await findResponse.json();
      const foundId = findData?.[0]?.ID || null;

      if (typeof foundId === 'string' && foundId.includes('.')) {
        const secondLookup = await orthancFetch({
          path: '/tools/lookup',
          method: 'POST',
          body: foundId,
        });
        if (secondLookup?.ok) {
          const secondData = await secondLookup.json();
          const studyMatch = Array.isArray(secondData)
            ? secondData.find(item => String(item?.Type || '').toLowerCase() === 'study')
            : null;
          if (studyMatch?.ID) {
            return studyMatch.ID;
          }
        }
      }

      return foundId;
    } catch (error) {
      return null;
    }
  };

  const getOrthancStudyIdFromSeries = async seriesInstanceUID => {
    if (!seriesInstanceUID) {
      return null;
    }

    try {
      const lookupResponse = await orthancFetch({
        path: '/tools/lookup',
        method: 'POST',
        body: seriesInstanceUID,
      });

      let orthancSeriesId = null;
      if (lookupResponse?.ok) {
        const lookupData = await lookupResponse.json();
        const seriesMatch = Array.isArray(lookupData)
          ? lookupData.find(item => String(item?.Type || '').toLowerCase() === 'series')
          : null;
        orthancSeriesId = seriesMatch?.ID || null;
      }

      if (!orthancSeriesId) {
        const findResponse = await orthancFetch({
          path: '/tools/find',
          method: 'POST',
          contentType: 'application/json',
          body: JSON.stringify({
            Level: 'Series',
            Expand: true,
            Query: { SeriesInstanceUID: seriesInstanceUID },
          }),
        });

        if (!findResponse?.ok) {
          return null;
        }
        const findData = await findResponse.json();
        // ParentStudy in Orthanc find series response is usually the internal study ID.
        const parentStudy = findData?.[0]?.ParentStudy || null;
        if (parentStudy) {
          return parentStudy;
        }
        orthancSeriesId = findData?.[0]?.ID || null;
      }

      if (!orthancSeriesId) {
        return null;
      }

      const seriesInfoResponse = await orthancFetch({
        path: `/series/${orthancSeriesId}`,
        method: 'GET',
        contentType: 'application/json',
      });

      if (!seriesInfoResponse?.ok) {
        return null;
      }

      const seriesInfo = await seriesInfoResponse.json();
      return seriesInfo?.ParentStudy || null;
    } catch (error) {
      return null;
    }
  };

  const getOrthancSeriesID = async seriesInstanceUID => {
    if (!seriesInstanceUID) {
      return null;
    }

    try {
      const lookupResponse = await orthancFetch({
        path: '/tools/lookup',
        method: 'POST',
        body: seriesInstanceUID,
      });

      if (lookupResponse?.ok) {
        const lookupData = await lookupResponse.json();
        const seriesMatch = Array.isArray(lookupData)
          ? lookupData.find(item => String(item?.Type || '').toLowerCase() === 'series')
          : null;
        if (seriesMatch?.ID) {
          return seriesMatch.ID;
        }
      }

      const findResponse = await orthancFetch({
        path: '/tools/find',
        method: 'POST',
        contentType: 'application/json',
        body: JSON.stringify({
          Level: 'Series',
          Expand: true,
          Query: { SeriesInstanceUID: seriesInstanceUID },
        }),
      });

      if (!findResponse?.ok) {
        return null;
      }

      const findData = await findResponse.json();
      return findData?.[0]?.ID || null;
    } catch (error) {
      return null;
    }
  };

  const getGroupFromSeriesDicom = async seriesID => {
    if (!seriesID) {
      return '';
    }

    try {
      const seriesResponse = await orthancFetch({
        path: `/series/${seriesID}`,
        method: 'GET',
        contentType: 'application/json',
      });

      if (!seriesResponse?.ok) {
        return '';
      }

      const seriesInfo = await seriesResponse.json();
      const firstInstanceId = seriesInfo?.Instances?.[0];
      if (!firstInstanceId) {
        return '';
      }

      const commentsResponse = await orthancFetch({
        path: `/instances/${firstInstanceId}/content/0020-4000`,
        method: 'GET',
      });

      if (!commentsResponse?.ok) {
        return '';
      }

      const raw = ((await commentsResponse.text()) || '').trim();
      const match = /^Group=(A|B)$/i.exec(raw);
      return match ? match[1].toUpperCase() : '';
    } catch (error) {
      return '';
    }
  };

  const getGroupFromStudyDicom = async studyID => {
    if (!studyID) {
      return '';
    }

    try {
      const studyResponse = await orthancFetch({
        path: `/studies/${studyID}`,
        method: 'GET',
        contentType: 'application/json',
      });

      if (!studyResponse?.ok) {
        return '';
      }

      const studyInfo = await studyResponse.json();
      const firstSeriesId = studyInfo?.Series?.[0];
      if (!firstSeriesId) {
        return '';
      }

      return await getGroupFromSeriesDicom(firstSeriesId);
    } catch (error) {
      return '';
    }
  };

  const resolveInternalStudyId = async studyInstanceUID => {
    const id = await getOrthancStudyID(studyInstanceUID);
    if (!id) {
      return null;
    }

    // Orthanc internal IDs are not DICOM UIDs. If it still looks like a UID,
    // force a lookup conversion; otherwise metadata endpoints may return 404.
    if (typeof id === 'string' && id.includes('.')) {
      const lookupResponse = await orthancFetch({
        path: '/tools/lookup',
        method: 'POST',
        body: id,
      });
      if (lookupResponse?.ok) {
        const lookupData = await lookupResponse.json();
        const studyMatch = Array.isArray(lookupData)
          ? lookupData.find(item => String(item?.Type || '').toLowerCase() === 'study')
          : null;
        return studyMatch?.ID || null;
      }
      return null;
    }

    return id;
  };

  const getMetadataOfStudy = async (studyID, type) => {
    if (!studyID || !['Prompt', 'Findings', 'Impressions', 'Group'].includes(type)) {
      return '';
    }

    const keys = METADATA_KEYS[type] || [type];
    for (const key of keys) {
      const response = await orthancFetch({
        path: `/studies/${studyID}/metadata/${key}`,
        method: 'GET',
      });

      if (!response?.ok) {
        continue;
      }

      const text = (await response.text()) || '';
      const trimmed = text.trim();
      if (/^<!doctype html/i.test(trimmed) || /^<html/i.test(trimmed)) {
        continue;
      }

      return text;
    }

    return '';
  };

  const getMetadataOfSeries = async (seriesID, type) => {
    if (!seriesID || type !== 'SeriesPrompt') {
      return '';
    }

    const response = await orthancFetch({
      path: `/series/${seriesID}/metadata/${type}`,
      method: 'GET',
    });

    if (!response?.ok) {
      return '';
    }

    const text = (await response.text()) || '';
    const trimmed = text.trim();
    if (/^<!doctype html/i.test(trimmed) || /^<html/i.test(trimmed)) {
      return '';
    }

    return text;
  };

  const addMetadataToStudy = async (studyID, data, type) => {
    if (!studyID || !['Findings', 'Impressions', 'Group'].includes(type)) {
      return { ok: false, status: 0 };
    }

    const keys = METADATA_KEYS[type] || [type];
    let lastStatus = 0;

    for (const key of keys) {
      const response = await orthancFetch({
        path: `/studies/${studyID}/metadata/${key}`,
        method: 'PUT',
        body: data || '',
      });

      lastStatus = response?.status ?? 0;
      if (response?.ok) {
        return { ok: true, status: response.status };
      }
    }

    return {
      ok: false,
      status: lastStatus,
    };
  };

  const loadReportForActiveStudy = useCallback(async () => {
    try {
      const requestId = ++loadRequestRef.current;
      const activeDisplaySet = getActiveDisplaySet();
      const isGenerativeRoute = window.location.pathname.includes('/generative-ai/');
      const urlStudyUID = getStudyUIDFromUrl();
      const studyInstanceUID = isGenerativeRoute
        ? urlStudyUID || activeDisplaySet?.StudyInstanceUID
        : activeDisplaySet?.StudyInstanceUID || urlStudyUID;
      const seriesInstanceUID = activeDisplaySet?.SeriesInstanceUID;
      const isPlaceholderStudy = studyInstanceUID === GENERATIVE_AI_PLACEHOLDER_STUDY_UID;

      // Reset immediately to avoid showing stale text from previously selected study
      // while async lookups are still running.
      setReportPromptData('');
      setReportFindingsData('');
      setReportImpressionsData('');
      setReportGroupData('None');
      setStatus('');

      setActiveStudyInstanceUID(studyInstanceUID || '');
      setActiveSeriesInstanceUID(seriesInstanceUID || '');

      if (!studyInstanceUID && !seriesInstanceUID) {
        setOrthancStudyID('');
        setIsImportedReadOnly(false);
        setStatus('Select an image to load report.');
        return;
      }

      // Placeholder study must always open with empty report fields.
      if (isPlaceholderStudy) {
        setOrthancStudyID('');
        setIsImportedReadOnly(false);
        setReportGroupData('None');
        return;
      }

      const seriesID = await getOrthancSeriesID(seriesInstanceUID);

      let studyID = await resolveInternalStudyId(studyInstanceUID);
      if (!studyID && !isGenerativeRoute) {
        studyID = await getOrthancStudyIdFromSeries(seriesInstanceUID);
      }
      setOrthancStudyID(studyID || '');

      if (!studyID) {
        setIsImportedReadOnly(false);
        setStatus('Unable to resolve study in Orthanc.');
        return;
      }

      const groupFromDisplaySet = getGroupFromDisplaySet(activeDisplaySet);

      const [studyPrompt, seriesPrompt, findings, impressions, group, groupFromDicom, groupFromStudyDicom] = await Promise.all([
        getMetadataOfStudy(studyID, 'Prompt'),
        getMetadataOfSeries(seriesID, 'SeriesPrompt'),
        getMetadataOfStudy(studyID, 'Findings'),
        getMetadataOfStudy(studyID, 'Impressions'),
        getMetadataOfStudy(studyID, 'Group'),
        getGroupFromSeriesDicom(seriesID),
        getGroupFromStudyDicom(studyID),
      ]);

      // Ignore stale async completion from older viewport/study selections.
      if (requestId !== loadRequestRef.current) {
        return;
      }

      const importedGroup = importedGroupMap?.[studyInstanceUID];
      const isImportedSample =
        importedGroup === 'A' ||
        importedGroup === 'B' ||
        isImportedSampleDisplaySet(activeDisplaySet);
      setIsImportedReadOnly(isImportedSample && !allowImportedReportEditing);
      setReportPromptData(isImportedSample ? '' : studyPrompt || seriesPrompt || '');
      setReportFindingsData(findings || '');
      setReportImpressionsData(impressions || '');
      const normalizedGroup =
        group === 'A' || group === 'B'
          ? group
          : groupFromDisplaySet === 'A' || groupFromDisplaySet === 'B'
            ? groupFromDisplaySet
          : groupFromDicom === 'A' || groupFromDicom === 'B'
            ? groupFromDicom
            : groupFromStudyDicom === 'A' || groupFromStudyDicom === 'B'
              ? groupFromStudyDicom
            : importedGroup === 'A' || importedGroup === 'B'
              ? importedGroup
            : getLocalStudyGroup(studyInstanceUID);
      setReportGroupData(normalizedGroup);
      setStatus('');
    } catch (error) {
      setStatus('Metadata panel error (viewer rendering continues).');
    }
  }, [allowImportedReportEditing, displaySetService, viewportGridService, importedGroupMap]);

  useEffect(() => {
    let cancelled = false;

    const loadGeneratedGroupMap = async () => {
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
        // Ignore missing generated map.
      }
    };

    loadGeneratedGroupMap();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    loadReportForActiveStudy();

    const displaySetSub = displaySetService.subscribe(
      displaySetService.EVENTS.DISPLAY_SETS_CHANGED,
      () => loadReportForActiveStudy()
    );
    const viewportSub = viewportGridService.subscribe(
      viewportGridService.EVENTS.ACTIVE_VIEWPORT_ID_CHANGED,
      () => loadReportForActiveStudy()
    );
    const viewportDataSub = cornerstoneViewportService?.subscribe
      ? cornerstoneViewportService.subscribe(
          cornerstoneViewportService.EVENTS.VIEWPORT_DATA_CHANGED,
          () => loadReportForActiveStudy()
        )
      : null;
    const viewportVolumesSub = cornerstoneViewportService?.subscribe
      ? cornerstoneViewportService.subscribe(
          cornerstoneViewportService.EVENTS.VIEWPORT_VOLUMES_CHANGED,
          () => loadReportForActiveStudy()
        )
      : null;

    return () => {
      displaySetSub.unsubscribe();
      viewportSub.unsubscribe();
      viewportDataSub?.unsubscribe?.();
      viewportVolumesSub?.unsubscribe?.();
    };
  }, [
    cornerstoneViewportService,
    displaySetService,
    viewportGridService,
    loadReportForActiveStudy,
    importedGroupMap,
  ]);

  const saveReport = async () => {
    const isGenerativeRoute = window.location.pathname.includes('/generative-ai/');
    const urlStudyUID = getStudyUIDFromUrl();
    const targetStudyUID = (isGenerativeRoute ? urlStudyUID : '') || activeStudyInstanceUID || urlStudyUID;
    const isPlaceholderStudy = targetStudyUID === GENERATIVE_AI_PLACEHOLDER_STUDY_UID;

    if (isPlaceholderStudy) {
      setStatus('Report disabled for placeholder study.');
      return;
    }

    let targetStudyID = await resolveInternalStudyId(
      targetStudyUID
    );
    if (!targetStudyID && !isGenerativeRoute) {
      targetStudyID = await getOrthancStudyIdFromSeries(activeSeriesInstanceUID);
    }

    if (!targetStudyID) {
      setStatus('Unable to save: study not found in Orthanc.');
      return;
    }
    setOrthancStudyID(targetStudyID);

    setIsSaving(true);
    setStatus('');

    try {
      const [f, i, g] = await Promise.all([
        addMetadataToStudy(targetStudyID, reportFindingsData, 'Findings'),
        addMetadataToStudy(targetStudyID, reportImpressionsData, 'Impressions'),
        addMetadataToStudy(targetStudyID, reportGroupData === 'A' || reportGroupData === 'B' ? reportGroupData : '', 'Group'),
      ]);
      setLocalStudyGroup(targetStudyUID, reportGroupData);

      if (f.ok && i.ok && g.ok) {
        setStatus('Report saved successfully.');
      } else {
        const failed = [
          !f.ok ? `Findings(${f.status})` : null,
          !i.ok ? `Impressions(${i.status})` : null,
          !g.ok ? `Group(${g.status})` : null,
        ]
          .filter(Boolean)
          .join(', ');
        setStatus(`Save failed: ${failed || 'unknown error'}.`);
      }
    } catch (error) {
      setStatus('Save failed due to runtime/network error.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="bg-black">
      <div className="bg-primary-dark flex flex-col justify-center p-4">
        {showPrompt ? (
          <>
            <div className="text-primary-main font-bold mb-2">Generation Prompt</div>
            <div
              className={`mb-4 text-sm whitespace-pre-wrap break-words min-h-[24px] ${
                reportPromptData ? 'text-white' : 'text-[#94a3b8]'
              }`}
            >
              {reportPromptData || 'Not available'}
            </div>
          </>
        ) : null}

        <div className="text-primary-main font-bold mb-2">Findings</div>
        <textarea
          rows={10}
          className="text-white text-[14px] leading-[1.2] border-primary-main bg-black align-top transition duration-300 appearance-none border border-inputfield-main focus:border-inputfield-focus focus:outline-none disabled:border-inputfield-disabled rounded w-full py-2 px-3 text-sm placeholder-inputfield-placeholder leading-tight mb-4"
          value={reportFindingsData}
          disabled={isImportedReadOnly}
          onChange={event => setReportFindingsData(event.target.value)}
          placeholder="Enter findings..."
        />

        <div className="text-primary-main font-bold mb-2">Impressions</div>
        <textarea
          rows={10}
          className="text-white text-[14px] leading-[1.2] border-primary-main bg-black align-top transition duration-300 appearance-none border border-inputfield-main focus:border-inputfield-focus focus:outline-none disabled:border-inputfield-disabled rounded w-full py-2 px-3 text-sm placeholder-inputfield-placeholder leading-tight"
          value={reportImpressionsData}
          disabled={isImportedReadOnly}
          onChange={event => setReportImpressionsData(event.target.value)}
          placeholder="Enter impressions..."
        />

        <div className="text-primary-main font-bold mt-4 mb-2">Group</div>
        <select
          className="text-white text-[14px] border-primary-main bg-black transition duration-300 appearance-none border border-inputfield-main focus:border-inputfield-focus focus:outline-none disabled:border-inputfield-disabled rounded w-full py-2 px-3 text-sm leading-tight"
          value={reportGroupData}
          onChange={event => setReportGroupData(event.target.value)}
        >
          <option value="None">None</option>
          <option value="A">A</option>
          <option value="B">B</option>
        </select>

        <div className="flex justify-center p-4 bg-primary-dark">
          <button
            type="button"
            onClick={saveReport}
            disabled={isSaving || isImportedReadOnly}
            className="h-[32px] rounded bg-primary-main px-4 text-white disabled:opacity-60"
          >
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        </div>

        {isImportedReadOnly ? (
          <div className="text-center text-xs text-[#94a3b8] mb-2">
            Preloaded reports are read-only in this deployment.
          </div>
        ) : null}
        {status ? <div className="text-center text-xs text-[#94a3b8]">{status}</div> : null}
      </div>
    </div>
  );
}

export default TextArea;
