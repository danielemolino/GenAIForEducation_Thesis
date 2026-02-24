import React, { useCallback, useEffect, useRef, useState } from 'react';

function TextArea({ servicesManager }) {
  const { displaySetService, viewportGridService } = servicesManager.services;

  const [orthancStudyID, setOrthancStudyID] = useState('');
  const [activeStudyInstanceUID, setActiveStudyInstanceUID] = useState('');
  const [activeSeriesInstanceUID, setActiveSeriesInstanceUID] = useState('');
  const [reportFindingsData, setReportFindingsData] = useState('');
  const [reportImpressionsData, setReportImpressionsData] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [status, setStatus] = useState('');
  const loadRequestRef = useRef(0);

  const orthancAuth = `Basic ${window.btoa('orthanc:orthanc')}`;
  const METADATA_KEYS = {
    Findings: ['Findings', '1024'],
    Impressions: ['Impressions', '1025'],
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
    const activeViewport = viewportState?.viewports?.get(activeViewportId);
    const activeDisplaySetUID = activeViewport?.displaySetInstanceUIDs?.[0];

    if (activeDisplaySetUID) {
      return displaySetService.getDisplaySetByUID(activeDisplaySetUID);
    }

    const activeDisplaySets = displaySetService.getActiveDisplaySets();
    return activeDisplaySets?.[0];
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
    if (!studyID || !['Findings', 'Impressions'].includes(type)) {
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

  const addMetadataToStudy = async (studyID, data, type) => {
    if (!studyID || !['Findings', 'Impressions'].includes(type)) {
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
    const requestId = ++loadRequestRef.current;
    const activeDisplaySet = getActiveDisplaySet();
    const studyInstanceUID = activeDisplaySet?.StudyInstanceUID;
    const seriesInstanceUID = activeDisplaySet?.SeriesInstanceUID;

    // Reset immediately to avoid showing stale text from previously selected study
    // while async lookups are still running.
    setReportFindingsData('');
    setReportImpressionsData('');
    setStatus('');

    setActiveStudyInstanceUID(studyInstanceUID || '');
    setActiveSeriesInstanceUID(seriesInstanceUID || '');

    if (!studyInstanceUID && !seriesInstanceUID) {
      setOrthancStudyID('');
      setStatus('Select an image to load report.');
      return;
    }

    // Prefer resolving by active Series -> ParentStudy to ensure we target
    // the exact generated object currently displayed.
    let studyID = await getOrthancStudyIdFromSeries(seriesInstanceUID);
    if (!studyID) {
      studyID = await resolveInternalStudyId(studyInstanceUID);
    }
    setOrthancStudyID(studyID || '');

    if (!studyID) {
      setStatus('Unable to resolve study in Orthanc.');
      return;
    }

    const [findings, impressions] = await Promise.all([
      getMetadataOfStudy(studyID, 'Findings'),
      getMetadataOfStudy(studyID, 'Impressions'),
    ]);

    // Ignore stale async completion from older viewport/study selections.
    if (requestId !== loadRequestRef.current) {
      return;
    }

    setReportFindingsData(findings || '');
    setReportImpressionsData(impressions || '');
    setStatus('');
  }, [displaySetService, viewportGridService]);

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

    return () => {
      displaySetSub.unsubscribe();
      viewportSub.unsubscribe();
    };
  }, [displaySetService, viewportGridService, loadReportForActiveStudy]);

  const saveReport = async () => {
    let studyID = await getOrthancStudyIdFromSeries(activeSeriesInstanceUID);
    if (!studyID) {
      studyID = await resolveInternalStudyId(activeStudyInstanceUID);
    }
    if (!studyID) {
      setStatus('Unable to save: study not found in Orthanc.');
      return;
    }
    setOrthancStudyID(studyID);

    setIsSaving(true);
    setStatus('');

    try {
      const [f, i] = await Promise.all([
        addMetadataToStudy(studyID, reportFindingsData, 'Findings'),
        addMetadataToStudy(studyID, reportImpressionsData, 'Impressions'),
      ]);

      if (f.ok && i.ok) {
        setStatus('Report saved successfully.');
      } else {
        const failed = [
          !f.ok ? `Findings(${f.status})` : null,
          !i.ok ? `Impressions(${i.status})` : null,
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
        <div className="text-primary-main font-bold mb-2">Findings</div>
        <textarea
          rows={10}
          className="text-white text-[14px] leading-[1.2] border-primary-main bg-black align-top transition duration-300 appearance-none border border-inputfield-main focus:border-inputfield-focus focus:outline-none disabled:border-inputfield-disabled rounded w-full py-2 px-3 text-sm placeholder-inputfield-placeholder leading-tight mb-4"
          value={reportFindingsData}
          onChange={event => setReportFindingsData(event.target.value)}
          placeholder="Enter findings..."
        />

        <div className="text-primary-main font-bold mb-2">Impressions</div>
        <textarea
          rows={10}
          className="text-white text-[14px] leading-[1.2] border-primary-main bg-black align-top transition duration-300 appearance-none border border-inputfield-main focus:border-inputfield-focus focus:outline-none disabled:border-inputfield-disabled rounded w-full py-2 px-3 text-sm placeholder-inputfield-placeholder leading-tight"
          value={reportImpressionsData}
          onChange={event => setReportImpressionsData(event.target.value)}
          placeholder="Enter impressions..."
        />

        <div className="flex justify-center p-4 bg-primary-dark">
          <button
            type="button"
            onClick={saveReport}
            disabled={isSaving}
            className="h-[32px] rounded bg-primary-main px-4 text-white disabled:opacity-60"
          >
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        </div>

        {status ? <div className="text-center text-xs text-[#94a3b8]">{status}</div> : null}
      </div>
    </div>
  );
}

export default TextArea;
