import React,{useEffect, useState} from 'react';
import {ActionButtons} from '@ohif/ui'
import { useTranslation } from 'react-i18next';
import WrappedPreviewStudyBrowser from './components/WrappedPreviewStudyBrowser'
import ServerStatus from './components/ServerStatus'
import axios from 'axios';

function GenerativeAIComponent({ commandsManager, extensionManager, servicesManager }) {
    const { t } = useTranslation('Common');
    const {displaySetService, uiModalService, viewportGridService} = servicesManager.services;
    const [promptData, setPromptData] = useState('');
    const [promptHeaderData, setPromptHeaderData] = useState('Generated, X');
    const [modelIsRunning, setModelIsRunning] = useState(false); // if model in generating images in backend
    const [isServerRunning, setIsServerRunning] = useState(false); // if connection to server possible
    const [dataIsUploading, setDataIsUploading] = useState(false); // if data is uploading to orthanc server
    const [oldModelIsRunning, setOldModelIsRunning] = useState(false);
    const [generatingFileSeriesInstanceUID, setGeneratingFileSeriesInstanceUID] = useState('');
    const [generatingFilePrompt, setGeneratingFilePrompt] = useState('');
    const [fileID, setFileID] = useState('');
    const [generationType, setGenerationType] = useState('');
    const [lastGeneratedStudyForViewer, setLastGeneratedStudyForViewer] = useState('');
    const [hasUsableInputStudy, setHasUsableInputStudy] = useState(false);
    const [allowedGenerationTypes, setAllowedGenerationTypes] = useState([]);
    const [serviceHealth, setServiceHealth] = useState({ ct: false, xray: false });

    const disabled = false;
    const MAX_STORED_STUDIES = 50;
    const GENERATIVE_AI_PLACEHOLDER_STUDY_UID =
      '1.2.826.0.1.3680043.8.498.92334923612841918328708913924036869452';
    const defaultServerCandidates = [
      'http://localhost:5000',
      'http://localhost:8000',
      'http://149.165.154.176:5000',
    ];
    const [serverUrl, setServerUrl] = useState(defaultServerCandidates[0]);
    const orthancAuth = `Basic ${window.btoa('orthanc:orthanc')}`;

    const _toServiceHealth = (servicesData, fallbackAllowedTypes = null) => {
      if (servicesData?.ct && servicesData?.xray) {
        return {
          ct: !!servicesData.ct.available,
          xray: !!servicesData.xray.available,
        };
      }
      if (Array.isArray(fallbackAllowedTypes)) {
        return {
          ct: fallbackAllowedTypes.includes('ct'),
          xray: fallbackAllowedTypes.includes('xray'),
        };
      }
      return { ct: true, xray: true };
    };

    const _checkServerCandidate = async candidateUrl => {
      try {
        const statusResponse = await axios.get(`${candidateUrl}/status`);
        if (statusResponse.status === 200) {
          let modelAvailable = true;
          let modeAllowedGenerationTypes = null;
          let currentServiceHealth = { ct: false, xray: false };
          try {
            const modeResponse = await axios.get(`${candidateUrl}/mode`);
            const allowed = modeResponse?.data?.allowedGenerationTypes;
            modeAllowedGenerationTypes = Array.isArray(allowed) ? allowed : null;
            currentServiceHealth = _toServiceHealth(modeResponse?.data?.services, modeAllowedGenerationTypes);
            if (Array.isArray(allowed) && allowed.length === 0) {
              modelAvailable = false;
            }
            if (modeResponse?.data?.backendMode === 'api-only') {
              modelAvailable = false;
            }
          } catch (modeError) {
            // Older backends may not expose /mode; keep legacy behavior.
            try {
              const servicesResponse = await axios.get(`${candidateUrl}/services/status`);
              currentServiceHealth = _toServiceHealth(servicesResponse?.data?.services, null);
              modelAvailable = currentServiceHealth.ct || currentServiceHealth.xray;
            } catch (servicesError) {
              modelAvailable = true;
            }
          }
          return {
            ok: true,
            processIsRunning: !!statusResponse.data?.process_is_running,
            modelAvailable,
            allowedGenerationTypes: modeAllowedGenerationTypes,
            serviceHealth: currentServiceHealth,
          };
        }
      } catch (statusError) {
        // Fallback for older backends without /status endpoint.
        try {
          const rootResponse = await axios.get(candidateUrl);
          if (rootResponse.status === 200) {
            return {
              ok: true,
              processIsRunning: false,
              modelAvailable: true,
              allowedGenerationTypes: null,
              serviceHealth: { ct: true, xray: true },
            };
          }
        } catch (rootError) {
          return {
            ok: false,
            processIsRunning: false,
            modelAvailable: false,
            allowedGenerationTypes: null,
            serviceHealth: { ct: false, xray: false },
          };
        }
      }
      return {
        ok: false,
        processIsRunning: false,
        modelAvailable: false,
        allowedGenerationTypes: null,
        serviceHealth: { ct: false, xray: false },
      };
    };

    // check server status
    useEffect(() => {
        const checkServerStatus = async () => {
          const uniqueCandidates = [...new Set([serverUrl, ...defaultServerCandidates])];
          let fallbackReachableUrl = null;
          for (const candidateUrl of uniqueCandidates) {
            const status = await _checkServerCandidate(candidateUrl);
            if (status.ok) {
              if (status.modelAvailable) {
                setServerUrl(candidateUrl);
                setIsServerRunning(true);
                setAllowedGenerationTypes(status.allowedGenerationTypes || []);
                setServiceHealth(status.serviceHealth || { ct: false, xray: false });
                return;
              }
              if (!fallbackReachableUrl) {
                fallbackReachableUrl = candidateUrl;
              }
            }
          }
          if (fallbackReachableUrl) {
            setServerUrl(fallbackReachableUrl);
          }
          setIsServerRunning(false);
          setAllowedGenerationTypes([]);
          setServiceHealth({ ct: false, xray: false });
        };

        checkServerStatus();
        const interval = setInterval(checkServerStatus, 50000); // Check every 50 seconds

        return () => clearInterval(interval); // Cleanup on component unmount
      }, []);

      // follow status of MedSyn: when finished download images from backend and upload to Orthanc
      useEffect(() => {
        const checkModelIsRunning = async () => {
            try {
                const status = await _checkServerCandidate(serverUrl);
                if (!status.ok) {
                  return;
                }
                if (Array.isArray(status.allowedGenerationTypes)) {
                  setAllowedGenerationTypes(status.allowedGenerationTypes);
                }
                if (status.serviceHealth) {
                  setServiceHealth(status.serviceHealth);
                }
                const processIsRunning = status.processIsRunning;
                setModelIsRunning((prevModelIsRunning) => {
                    if (prevModelIsRunning === false && processIsRunning === true) {
                        console.log("Model started");
                    } else if (prevModelIsRunning === true && processIsRunning === false) {
                        console.log("Model ended");
                        console.log("Try to download data");

                        executeDownloadAndUpload();
                    }
                    setOldModelIsRunning(prevModelIsRunning);
                    return processIsRunning;
                });
            } catch (error) {
                console.log('Error checking for model status:', error);
            }
        };
        const executeDownloadAndUpload = async () => {
          try {
              let uploadResults;
              try {
                uploadResults = await _uploadGeneratedFolderFromBackend(fileID);
              } catch (serverSideUploadError) {
                // Fallback for older backend versions without upload endpoint.
                uploadResults = await _downloadAndUploadImages(fileID);
              }
              try {
                await _enforceOrthancStudyRetention();
              } catch (retentionError) {
                console.warn('Orthanc retention cleanup failed, continuing:', retentionError);
              }
              try {
                await _addMetadataToSeries(generatingFileSeriesInstanceUID, generatingFilePrompt, 'SeriesPrompt');
              } catch (metadataError) {
                console.warn('Series metadata update failed, continuing:', metadataError);
              }
              const generatedStudyInstanceUID = await _resolveGeneratedStudyInstanceUID(
                uploadResults,
                generatingFileSeriesInstanceUID
              );
              try {
                const generatedStudyOrthancID = _resolveGeneratedStudyOrthancID(uploadResults);
                await _addMetadataToStudy(
                  generatedStudyInstanceUID,
                  generatingFilePrompt,
                  'Prompt',
                  generatedStudyOrthancID
                );
              } catch (metadataError) {
                console.warn('Study metadata update failed, continuing:', metadataError);
              }
              const generatedSeriesInstanceUID = await _resolveGeneratedSeriesInstanceUID(
                uploadResults
              );
              await _waitForGeneratedDataInOrthanc(
                generatedStudyInstanceUID,
                generatedSeriesInstanceUID
              );
              await _openGeneratedStudy(generatedStudyInstanceUID, generatedSeriesInstanceUID);

          } catch (error) {
              console.error('Error in downloading and uploading images:', error);
              showErrorFeedback(error);
          }
        };
        const reloadWindow = () => {
          
          console.log('Modal is closing');        
          window.location.reload();
        };
        const showErrorFeedback=(error)=>{
          const status = error?.response?.status;
          const body = error?.response?.data;
          const errorText =
            (typeof body === 'string' && body) ||
            body?.error ||
            body?.message ||
            error?.message ||
            'Unknown error';
          uiModalService.show({
            title: 'Error with Image Generation ',
            containerDimensions: 'w-1/2',
            content: () => {
              const details =
                `Generation may have completed on backend, but upload to Orthanc failed. Status: ${status || 'n/a'} | ${errorText}`;
              return (
                <div>
                  <p className="mt-2 p-2">
                    The backend CUDA may be out of memory. Please try clicking "Generate new CT" again and hope that the backend server has less load this time. 
                  </p>
                  <div className="mt-2 p-2 text-red-600">{details}</div>
                </div>
              );
            },
          });

        }

        const showSuccessFeedback = () => {
            return new Promise((resolve) => {
                uiModalService.show({
                    title: 'Info',
                    containerDimensions: 'w-1/2',

                    content: () => {
                        return (
                            <div>
                                <p className="mt-2 p-2 mb-8">
                                  The CT scan was generated successfully
                                </p>
                                <div className="flex items-center p-2 ml-8">
                                  <div className="text-primary-main  mr-2">Name:</div>
                                  <div className="text-blue-300  mr-2">{promptHeaderData}</div>
                                </div>
                                <div className="flex flex-col mb-8 p-2 ml-8">
                                  <div className="text-primary-main  mr-2">Prompt:</div>
                                  <div className="mr-2">{promptData}</div>
                                </div>
                                  <ActionButtons
                                      t={t}
                                      actions={[

                                          {
                                              label: 'Ok',
                                              onClick: reloadPage,
                                          },
                                      ]}
                                      disabled={disabled}
                                  />
                              
                            </div>
                        );
                    },

                });
            });
        };

        checkModelIsRunning();
        const interval = setInterval(checkModelIsRunning, 5000); // Check every 5 seconds

        return () => clearInterval(interval); // Cleanup on component unmount
    }, [fileID, generatingFileSeriesInstanceUID, serverUrl]);


    // update text of previews
    useEffect(() => {
      // run when component is mounted at least once to avoid empty text when closing and reopening tab
      _handleDisplaySetsChanged();
      try {
        const storedStudyUID = localStorage.getItem('lastGeneratedStudyInstanceUID') || '';
        if (storedStudyUID && storedStudyUID !== GENERATIVE_AI_PLACEHOLDER_STUDY_UID) {
          setLastGeneratedStudyForViewer(storedStudyUID);
        }
      } catch (e) {
        // Ignore storage access errors.
      }
      _enforceOrthancStudyRetention().catch(error => {
        console.warn('Orthanc retention cleanup on mount failed:', error);
      });
      // Subscribe to the DISPLAY_SETS_CHANGED event
      const displaySetSubscription = displaySetService.subscribe(
          displaySetService.EVENTS.DISPLAY_SETS_CHANGED,
          _handleDisplaySetsChanged
      );

      // Unsubscribe from the event when the component unmounts
      return () => {
          displaySetSubscription.unsubscribe();
      };
    }, []);

    const _extractApiError = error => {
      return (
        error?.response?.data?.error ||
        error?.response?.data?.detail ||
        error?.message ||
        'Unexpected error'
      );
    };

    const _hasActiveStudy = () => {
      const activeDisplaySets = displaySetService.getActiveDisplaySets() || [];
      return activeDisplaySets.some(set => {
        const studyUID = set?.StudyInstanceUID;
        return !!studyUID;
      });
    };
    const _isPlaceholderStudyLoadedInUrl = () => {
      try {
        const currentUrl = new URL(window.location.href);
        const studyUIDs = currentUrl.searchParams.getAll('StudyInstanceUIDs');
        return (
          studyUIDs.length > 0 &&
          studyUIDs.every(uid => uid === GENERATIVE_AI_PLACEHOLDER_STUDY_UID)
        );
      } catch (error) {
        return false;
      }
    };

    const handleGenerateClick = async (targetType = 'ct') => {
      try {
        if (!_hasActiveStudy()) {
          uiModalService.show({
            title: 'Error with Image Generation',
            containerDimensions: 'w-1/2',
            content: () => (
              <div>
                <p className="mt-2 p-2">
                  Open a study before generating.
                </p>
              </div>
            ),
          });
          return;
        }

        if (!promptData?.trim()) {
          uiModalService.show({
            title: 'Error with Image Generation',
            containerDimensions: 'w-1/2',
            content: () => (
              <div>
                <p className="mt-2 p-2">Please enter a prompt before generating.</p>
              </div>
            ),
          });
          return;
        }

        // get information about the current study
        const activeDisplaySets = displaySetService.getActiveDisplaySets() || [];
        const studyInstanceUIDs = activeDisplaySets.map(set => set.StudyInstanceUID).filter(Boolean);
        const studyInstanceUID = studyInstanceUIDs[0];

        if (!studyInstanceUID) {
          uiModalService.show({
            title: 'Error with Image Generation',
            containerDimensions: 'w-1/2',
            content: () => (
              <div>
                <p className="mt-2 p-2">
                  No active study is available in the viewport. Open a study first, then try again.
                </p>
              </div>
            ),
          });
          return;
        }

        const currentStudy = await _getOrthancStudyByID(studyInstanceUID);
        const patientName = currentStudy?.PatientMainDicomTags?.PatientName || 'Generated^Patient';
        const patientID = currentStudy?.PatientMainDicomTags?.PatientID || 'GEN-001';

        const firstTenLetters = promptData.replace(/[^a-zA-Z]/g, '').slice(0, 10);
        const formattedDate = _generateUniqueTimestamp();
        let currentFileID = `${formattedDate}${firstTenLetters}`; // e.g. YYYYMMDDHHMMSSCardiomega

        setFileID(currentFileID);

        const payload = {
          filename: `${currentFileID}.npy`,
          prompt: promptData || null,
          description: promptHeaderData,
          patient_name: patientName,
          patient_id: patientID,
          generationType: targetType,
        };
        const headers = {
          'Content-Type': 'application/json',
        };

        const response = await axios.post(`${serverUrl}/files/${currentFileID}`, payload, { headers });
        console.log('Start model');
        setGeneratingFilePrompt(payload.prompt || '');
        setGeneratingFileSeriesInstanceUID(response.data.seriesInstanceUID);
        try {
          localStorage.setItem('lastGeneratedPrompt', payload.prompt || '');
          localStorage.setItem('lastGeneratedPromptSeriesInstanceUID', response.data.seriesInstanceUID || '');
        } catch (e) {
          // Ignore storage errors
        }
      } catch (error) {
        const apiError =
          error?.response?.data?.error ||
          error?.response?.data?.detail ||
          error?.message ||
          'Unexpected error';

        uiModalService.show({
          title: 'Error with Image Generation',
          containerDimensions: 'w-1/2',
          content: () => {
            return (
              <div>
                <div className="text-red-600 mt-2 p-2">Error: {apiError}</div>
              </div>
            );
          },
        });
      }
    };

    const handlePromptHeaderChange = (event) => {
        setPromptHeaderData(event.target.value);
    };

    const handlePromptChange = (event) => {
        setPromptData(event.target.value);
    };
    const clearText = (event) => {
        setPromptData('');
    }

    const _uploadGeneratedFolderFromBackend = async folderName => {
      const response = await axios.post(`${serverUrl}/files/${folderName}/upload-orthanc`);
      return response?.data?.uploadResults || [];
    };

    const reloadPage = async (event) => {
      window.location.reload(); // TODO: change this dirty hack

    }

    const _openGeneratedStudy = async (studyInstanceUID, seriesInstanceUID = null) => {
      if (!studyInstanceUID) {
        return;
      }

      try {
        localStorage.setItem('lastGeneratedStudyInstanceUID', studyInstanceUID);
        setLastGeneratedStudyForViewer(studyInstanceUID);
        if (seriesInstanceUID) {
          localStorage.setItem('lastGeneratedSeriesInstanceUID', seriesInstanceUID);
        } else {
          localStorage.removeItem('lastGeneratedSeriesInstanceUID');
        }
      } catch (e) {
        // Ignore storage errors (private mode/quota/etc.)
      }

      const currentUrl = new URL(window.location.href);
      // Open only the generated study to avoid unresolved mixed-study URLs that can stall loading.
      currentUrl.searchParams.delete('StudyInstanceUIDs');
      currentUrl.searchParams.append('StudyInstanceUIDs', studyInstanceUID);

      let validSeriesInstanceUID = null;
      if (seriesInstanceUID) {
        const generatedSeries = await _getOrthancSeriesByID(seriesInstanceUID);
        if (generatedSeries?.ID) {
          validSeriesInstanceUID = seriesInstanceUID;
        }
      }

      if (validSeriesInstanceUID) {
        currentUrl.searchParams.delete('SeriesInstanceUIDs');
        currentUrl.searchParams.append('SeriesInstanceUIDs', validSeriesInstanceUID);
        currentUrl.searchParams.set('initialSeriesInstanceUID', validSeriesInstanceUID);
      } else {
        currentUrl.searchParams.delete('SeriesInstanceUIDs');
        currentUrl.searchParams.delete('initialSeriesInstanceUID');
      }
      currentUrl.searchParams.delete('initialSopInstanceUID');
      const destination = `${currentUrl.pathname}?${currentUrl.searchParams.toString()}`;
      // Use direct browser navigation to avoid React DnD HTML5 backend duplication
      // observed with in-app history navigation in this panel composition.
      window.location.assign(destination);
    };

    const _openBasicViewerForLastGeneratedStudy = () => {
      if (_isPlaceholderStudyLoadedInUrl()) {
        return;
      }

      let targetStudyUID = lastGeneratedStudyForViewer;
      try {
        if (!targetStudyUID) {
          targetStudyUID = localStorage.getItem('lastGeneratedStudyInstanceUID') || '';
        }
      } catch (e) {
        // Ignore storage errors.
      }

      if (!targetStudyUID || targetStudyUID === GENERATIVE_AI_PLACEHOLDER_STUDY_UID) {
        return;
      }

      const currentUrl = new URL(window.location.href);
      currentUrl.pathname = currentUrl.pathname.replace('/generative-ai/', '/viewer/');
      currentUrl.searchParams.delete('StudyInstanceUIDs');
      currentUrl.searchParams.delete('SeriesInstanceUIDs');
      currentUrl.searchParams.delete('initialSeriesInstanceUID');
      currentUrl.searchParams.delete('initialSopInstanceUID');
      currentUrl.searchParams.append('StudyInstanceUIDs', targetStudyUID);

      const destination = `${currentUrl.pathname}?${currentUrl.searchParams.toString()}`;
      window.location.assign(destination);
    };
    const canOpenBasicViewer =
      !_isPlaceholderStudyLoadedInUrl() &&
      !!lastGeneratedStudyForViewer &&
      lastGeneratedStudyForViewer !== GENERATIVE_AI_PLACEHOLDER_STUDY_UID;

    const _downloadAndUploadImages = async (fileID) => {
      try {
          console.log("downloadAndUploadImages fileID: ", fileID);
          const files = (await _getFilesFromFolder(fileID)).sort();

          setDataIsUploading(true);
          const uploadResults = await _runWithConcurrency(files, 6, async filename => {
              const blob = await _fetchDicomFile(fileID, filename);
              if (!blob) {
                throw new Error(`Unable to fetch generated DICOM: ${filename}`);
              }
              return _uploadDicomToOrthanc(blob);
          });
          setDataIsUploading(false); // Ensure this is called after all files are processed
          console.log('All files are uploaded', dataIsUploading);
          return uploadResults;
      } catch (error) {
          console.error('Error in Downloading dicom images from server:', error);
          setDataIsUploading(false); // Ensure this is called in case of an error
          throw error
      }
    };




    const _getFilesFromFolder = async (foldername) => {
        try {
          const response = await axios.get(`${serverUrl}/files/${foldername}`);
          return response.data;  // Assuming the response is a list of files
        } catch (error) {
          console.error("Error fetching files:", error.response ? error.response.data.error : error.message);
          throw error;  // Rethrow the error to handle it in the calling code if needed
        }
    };
    const _fetchDicomFile = async (foldername, filename) => {
        try {
          const response = await axios.post(`${serverUrl}/files/${foldername}/${filename}`, {
            data: 'example'
          }, {
            headers: {
                'Content-Type': 'application/json'
              },
            responseType: 'arraybuffer'
          });

            const arrayBuffer = response.data
            const blob = new Blob([arrayBuffer], { type: 'application/dicom' });
            return blob;


        } catch (error) {
          console.error('There was an error!', error);
          return null;
        }
      };

    const _uploadDicomToOrthanc = async (blob) => {
        try {
            // Orthanc /instances expects raw DICOM payload.
            const orthancResponse = await axios.post('/pacs/instances', blob, {
            headers: {
                'Content-Type': 'application/dicom',
                Authorization: orthancAuth,
            }
            });
            return orthancResponse.data;

        } catch (error) {
          console.error('Error uploading DICOM file to Orthanc:', error);
          // Propagate the upload error so the caller can stop the flow and show a real error.
          throw error;
        }
    };

    const _enforceOrthancStudyRetention = async () => {
      const response = await fetch('/pacs/studies?expand=1', {
        headers: {
          Authorization: orthancAuth,
        },
      });

      if (!response.ok) {
        throw new Error(`Unable to query studies for retention (status ${response.status})`);
      }

      const studies = await response.json();
      const filteredStudies = Array.isArray(studies)
        ? studies.filter(study => {
            const tags = study?.RequestedTags || study?.MainDicomTags || {};
            return tags?.StudyInstanceUID !== GENERATIVE_AI_PLACEHOLDER_STUDY_UID;
          })
        : [];

      if (filteredStudies.length <= MAX_STORED_STUDIES) {
        return;
      }

      const normalized = filteredStudies
        .filter(study => study?.ID)
        .map(study => {
          const tags = study?.RequestedTags || study?.MainDicomTags || {};
          const studyDate = String(tags?.StudyDate || '').replace(/\D/g, '').slice(0, 8);
          const studyTime = String(tags?.StudyTime || '')
            .replace(/\D/g, '')
            .slice(0, 6)
            .padEnd(6, '0');
          const sortKey = `${studyDate}${studyTime}`.padEnd(14, '0');
          return { id: study.ID, sortKey };
        })
        .sort((a, b) => a.sortKey.localeCompare(b.sortKey));

      const toDeleteCount = Math.max(0, normalized.length - MAX_STORED_STUDIES);
      const studiesToDelete = normalized.slice(0, toDeleteCount);

      for (const study of studiesToDelete) {
        const deleteResponse = await fetch(`/pacs/studies/${study.id}`, {
          method: 'DELETE',
          headers: {
            Authorization: orthancAuth,
          },
        });

        if (!deleteResponse.ok) {
          console.warn(`Failed to delete study ${study.id}. Status: ${deleteResponse.status}`);
        }
      }
    };

    const _getOrthancStudyInstanceUIDByOrthancID = async studyOrthancID => {
      if (!studyOrthancID) {
        return null;
      }

      try {
        const response = await fetch(`/pacs/studies/${studyOrthancID}`, {
          headers: {
            Authorization: orthancAuth,
          },
        });

        if (!response.ok) {
          return null;
        }

        const data = await response.json();
        return data?.MainDicomTags?.StudyInstanceUID || data?.RequestedTags?.StudyInstanceUID || null;
      } catch (error) {
        console.error('Error fetching study by Orthanc ID:', error);
        return null;
      }
    };

    const _runWithConcurrency = async (items, concurrency, taskFn) => {
      const results = new Array(items.length);
      let cursor = 0;

      const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
        while (true) {
          const currentIndex = cursor;
          cursor += 1;
          if (currentIndex >= items.length) {
            return;
          }
          results[currentIndex] = await taskFn(items[currentIndex]);
        }
      });

      await Promise.all(workers);
      return results;
    };

    const _resolveGeneratedStudyInstanceUID = async (uploadResults, seriesInstanceUID) => {
      const firstResultWithStudy = (uploadResults || []).find(result => result?.ParentStudy);
      if (firstResultWithStudy?.ParentStudy) {
        const uid = await _getOrthancStudyInstanceUIDByOrthancID(firstResultWithStudy.ParentStudy);
        if (uid) {
          return uid;
        }
      }

      // Fallback: resolve via generated series UID.
      for (let attempt = 0; attempt < 8; attempt++) {
        const generatedSeries = await _getOrthancSeriesByID(seriesInstanceUID);
        const uid = generatedSeries?.RequestedTags?.StudyInstanceUID;
        if (uid) {
          return uid;
        }
        await new Promise(resolve => setTimeout(resolve, 800));
      }

      return null;
    };
    const _resolveGeneratedStudyOrthancID = uploadResults => {
      const firstResultWithStudy = (uploadResults || []).find(result => result?.ParentStudy);
      return firstResultWithStudy?.ParentStudy || null;
    };

    const _resolveGeneratedSeriesInstanceUID = async uploadResults => {
      const firstResultWithSeries = (uploadResults || []).find(result => result?.ParentSeries);
      if (firstResultWithSeries?.ParentSeries) {
        const seriesUID = await _getOrthancSeriesInstanceUIDByOrthancID(firstResultWithSeries.ParentSeries);
        if (seriesUID) {
          return seriesUID;
        }
      }
      return null;
    };

    const _waitForGeneratedDataInOrthanc = async (studyInstanceUID, seriesInstanceUID = null) => {
      if (!studyInstanceUID) {
        return;
      }

      for (let attempt = 0; attempt < 15; attempt++) {
        const study = await _getOrthancStudyByID(studyInstanceUID);
        if (!study?.ID) {
          await new Promise(resolve => setTimeout(resolve, 500));
          continue;
        }

        if (!seriesInstanceUID) {
          return;
        }

        const series = await _getOrthancSeriesByID(seriesInstanceUID);
        if (series?.ID) {
          return;
        }

        await new Promise(resolve => setTimeout(resolve, 500));
      }
    };

    const _getOrthancStudyByID = async (studyInstanceUID) => {
      try {
          const response = await fetch('/pacs/tools/find', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: orthancAuth,
            },
            body: JSON.stringify({
              Level: 'Study',
              Expand: true,
              Query: {
                StudyInstanceUID: studyInstanceUID,
              },
            }),
          });

          if (!response.ok) {
            throw new Error('Network response was not ok');
          }

          const data = await response.json();
          const study = data?.[0];

          if (study) {
            return study;
          } else {
            console.error("No study found with studyInstanceUID: ",studyInstanceUID )
            return null;
          }

        } catch (error) {
          console.error('There has been a problem with _getOrthancStudyByID:', error);
          return null;
        }
      };
    const _getOrthancSeriesByID = async (seriesInstanceUID) => {
      try {
          const response = await fetch('/pacs/tools/find', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: orthancAuth,
            },
            body: JSON.stringify({
              Level: 'Series',
              Expand: true,
              Query: {
                SeriesInstanceUID: seriesInstanceUID,
              },
            }),
          });

          if (!response.ok) {
            throw new Error('Network response was not ok');
          }

          const data = await response.json();
          const study = data?.[0];


          if (study) {
            return study;
          } else {
            console.error("No series found with no seriesInstanceUID: ",seriesInstanceUID )
            return null;
          }
        } catch (error) {
          // Log any errors that occur during the fetch operation
          console.error('There has been a problem with your fetch operation:', error);
          return null;
        }
      };
    const _addMetadataToSeries = async (seriesInstanceUid, data, type) => {
        if (type !== 'SeriesPrompt') {
            console.log(`Invalid metadata type: ${type}.`);
            return;
        }

        try {

            const generatedSeries =  await _getOrthancSeriesByID(seriesInstanceUid);
            if (!generatedSeries?.ID) {
              throw new Error(`Generated series not found for UID: ${seriesInstanceUid}`);
            }

            const generatedSeriesOrthancID = generatedSeries.ID;

            const url = `/pacs/series/${generatedSeriesOrthancID}/metadata/${type}`;
            const headers = {
                'Content-Type': 'text/plain', // Ensure the server expects text/plain content type
                Authorization: orthancAuth,
            };

            const response = await axios.put(url, data, { headers });

            if (response.status !== 200) {
                throw new Error(`Metadata write failed. Status: ${response.status}`);
            }
        } catch (error) {
            console.log(`There was a problem with your fetch operation: ${error}`);
            throw error;
        }
      };
    const _addMetadataToStudy = async (studyInstanceUid, data, type, providedOrthancStudyID = null) => {
      const metadataKeysByType = {
        Prompt: ['Prompt', '1026'],
        Findings: ['Findings', '1024'],
        Impressions: ['Impressions', '1025'],
      };
      const keys = metadataKeysByType[type] || [];
      if (keys.length === 0) {
        return;
      }

      try {
        let orthancStudyID = providedOrthancStudyID;
        if (!orthancStudyID && studyInstanceUid) {
          const generatedStudy = await _getOrthancStudyByID(studyInstanceUid);
          orthancStudyID = generatedStudy?.ID || null;
        }
        if (!orthancStudyID) {
          throw new Error(`Generated study not found for UID: ${studyInstanceUid}`);
        }

        const headers = {
          'Content-Type': 'text/plain',
          Authorization: orthancAuth,
        };

        let anyWriteSucceeded = false;
        for (const key of keys) {
          try {
            await axios.put(`/pacs/studies/${orthancStudyID}/metadata/${key}`, data || '', { headers });
            anyWriteSucceeded = true;
          } catch (writeError) {
            console.warn(`Study metadata write failed for key ${key}:`, writeError);
          }
        }

        if (!anyWriteSucceeded) {
          throw new Error(`Unable to persist ${type} metadata on study.`);
        }
      } catch (error) {
        console.log(`There was a problem with study metadata update: ${error}`);
        throw error;
      }
    };
    const _getOrthancSeriesInstanceUIDByOrthancID = async seriesOrthancID => {
      if (!seriesOrthancID) {
        return null;
      }

      try {
        const response = await fetch(`/pacs/series/${seriesOrthancID}`, {
          headers: {
            Authorization: orthancAuth,
          },
        });

        if (!response.ok) {
          return null;
        }

        const data = await response.json();
        return data?.MainDicomTags?.SeriesInstanceUID || data?.RequestedTags?.SeriesInstanceUID || null;
      } catch (error) {
        console.error('Error fetching series by Orthanc ID:', error);
        return null;
      }
    };
    const _handleDisplaySetsChanged = async (changedDisplaySets = null) => {
        const activeDisplaySets = displaySetService.getActiveDisplaySets();
        setHasUsableInputStudy(_hasActiveStudy());
        // set initial prompt header to "Generated, NOT_USED_NUMBER"
        const seriesDescriptions = activeDisplaySets.map(set => set.SeriesDescription);
        const seriesDescriptionNumbers = _extractNumbers(seriesDescriptions);
        const maxNumber = Math.max(...seriesDescriptionNumbers);
        setPromptHeaderData(`Generated, ${maxNumber+1}`)

    };
    const normalizedGenerationType = generationType === 'xrays' ? 'xray' : generationType;
    const selectedTypeServiceUp =
      !normalizedGenerationType ||
      (normalizedGenerationType === 'ct' ? serviceHealth.ct : serviceHealth.xray);
    const selectedTypeAllowed =
      !normalizedGenerationType ||
      allowedGenerationTypes.length === 0 ||
      allowedGenerationTypes.includes(normalizedGenerationType);
    const generationReady = isServerRunning && selectedTypeAllowed && selectedTypeServiceUp;


    return (
        <div className="ohif-scrollbar flex flex-col">
            <div className="flex flex-col justify-center p-4 bg-primary-dark">

                <div className="flex items-center mb-2">
                    <div className="text-primary-main  mr-2">Name:</div>
                    <input
                        id="promptHeader"
                        className="bg-transparent break-all text-base text-blue-300"
                        type="text"
                        value={promptHeaderData}
                        onChange={handlePromptHeaderChange}
                        disabled= {modelIsRunning ||   dataIsUploading}
                    />
                </div>

                <textarea
                    rows={6}
                    placeholder="Enter Text to generate image ..."
                    className="text-white text-[14px] leading-[1.2] border-primary-main bg-black align-top sshadow transition duration-300 appearance-none border border-inputfield-main focus:border-inputfield-focus focus:outline-none disabled:border-inputfield-disabled rounded w-full py-2 px-3 text-sm text-white placeholder-inputfield-placeholder leading-tight"
                    value={promptData}
                    onChange={handlePromptChange}
                    disabled= {modelIsRunning ||   dataIsUploading}
                >
                </textarea>

                <div className="flex justify-center p-2 pb-8 bg-primary-dark">
                    <div className="mr-3">
                        <select
                            className="h-[32px] rounded border border-inputfield-main bg-black px-2 text-sm text-white"
                            value={generationType}
                            onChange={event => {
                              const selected = event.target.value;
                              setGenerationType(selected);
                            }}
                            disabled={modelIsRunning || dataIsUploading}
                        >
                            <option value="" disabled>
                              Select generation type
                            </option>
                            <option value="ct" disabled={(allowedGenerationTypes.length > 0 && !allowedGenerationTypes.includes('ct')) || !serviceHealth.ct}>
                              Generate CT
                            </option>
                            <option value="xrays" disabled={(allowedGenerationTypes.length > 0 && !allowedGenerationTypes.includes('xray')) || !serviceHealth.xray}>
                              Generate X-RAYS
                            </option>
                        </select>
                    </div>
                    <ActionButtons
                        t={t}
                        actions={[
                            {
                                label: 'Generate',
                                onClick: () => handleGenerateClick(generationType || 'ct'),
                                disabled:
                                  !generationType ||
                                  modelIsRunning ||
                                  !generationReady ||
                                  dataIsUploading ||
                                  !hasUsableInputStudy,
                            },
                            {
                                label: 'Clear',
                                onClick: clearText,
                            },
                        ]}
                        disabled={disabled}
                    />
                </div>

                <ServerStatus
                    modelIsRunning={modelIsRunning}
                    dataIsUploading={dataIsUploading}
                    isServerRunning={isServerRunning}
                    serverUrl={serverUrl}
                    serviceHealth={serviceHealth}
                />
                <div className="mt-3 flex justify-end">
                    <button
                        type="button"
                        onClick={_openBasicViewerForLastGeneratedStudy}
                        disabled={!canOpenBasicViewer}
                        className={`h-[32px] rounded px-3 text-sm text-white ${
                          canOpenBasicViewer
                            ? 'bg-primary-main hover:bg-primary-light'
                            : 'bg-inputfield-placeholder text-common-light cursor-default'
                        }`}
                    >
                        Basic Viewer
                    </button>
                </div>

            </div>

            {/* dif line */}
            <div className="border border-primary-main"> </div>
            <div className="mx-auto w-9/10">
                <WrappedPreviewStudyBrowser
                    commandsManager={commandsManager}
                    extensionManager={extensionManager}
                    servicesManager={servicesManager}
                    activatedTabName="ai"
                />
            </div>
        </div>

    );

    // Function to extract numbers from the array
    function _extractNumbers(arr) {
        // Use reduce to accumulate numbers in a single array
        return arr.reduce((acc, str) => {
        // Match all sequences of digits
        const matches = str.match(/\d+/g);
        if (matches) {
            // Convert matched strings to numbers and add to accumulator
            return acc.concat(matches.map(Number));
        }
        return acc;
        }, [0]);
    };
    function _generateUniqueTimestamp(){
      const date = new Date();
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      const hours = String(date.getHours()).padStart(2, '0');
      const minutes = String(date.getMinutes()).padStart(2, '0');
      const seconds = String(date.getSeconds()).padStart(2, '0');
      const formattedDate = `${year}${month}${day}${hours}${minutes}${seconds}`;
      return formattedDate;
    }
}


export default GenerativeAIComponent;

