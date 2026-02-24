import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';
import UserFeedback from './UserFeedback';

const StudyMetadataDisplay = ({
    description,
    onClick,
    onDoubleClick,
    seriesInstanceUID,
    modality
 }) => {
  const [promptMetaData, setPromptMetaData] = useState("");
  const [seriesID, setSeriesID] = useState("");

  useEffect(() => {
    if (modality ==='AI'){
      const fetchMetadata = async () => {
        const orthancSeriesID = await _getOrthancSeriesID(seriesInstanceUID);
        setSeriesID(orthancSeriesID);
        
        const response = await _getPromptMetadataOfSeries(orthancSeriesID);
        
        setPromptMetaData(response);
      };
  
      fetchMetadata();
    }

  }, [seriesInstanceUID, modality]);
  

  
  if (modality !== 'AI') return (
    <div className="group mb-8 flex flex-1 cursor-pointer flex-col px-3 outline-none"
      onClick={onClick}
      onDoubleClick={onDoubleClick}>
      <span className="text-primary-main font-bold select-none mb-1">{description}</span>
      
      
  </div>
  );

  return (
    <div className="group mb-8 flex flex-1 cursor-pointer flex-col px-3 outline-none"
          onClick={onClick}
          onDoubleClick={onDoubleClick}>
      <span className="text-primary-main font-bold select-none mb-1">{description}</span>
      <div className="break-all text-base text-blue-300 mt-1">Prompt: </div>
      <div className="break-words text-base text-white">
        {promptMetaData ? promptMetaData : ''}
      </div>
      <UserFeedback seriesID={seriesID} />
    </div>
  );
};

StudyMetadataDisplay.propTypes = {
  impressions: PropTypes.string,
};


const _getPromptMetadataOfSeries = async (seriesID) => {
  try {
    const url = `/pacs/series/${seriesID}/metadata/SeriesPrompt`;
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'text/plain'  // Ensure the server expects text/plain content type
      }
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.log("Response not ok. Status:", response.status, "Response text:", errorText);
      return;
    } else {
      return response.text();
    }

  } catch (error) {
    console.error('There was a problem with your fetch operation:', error);
  }
};
const _getOrthancSeriesID = async (seriesInstanceUID) => {
  try {
      const response = await fetch('/pacs/tools/find', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          Level: 'Series',
          Expand: true,
          Query: {
            SeriesInstanceUID: seriesInstanceUID,
          },
        }),
      });
  
      // Check if the response is ok (status code 200-299)
      if (!response.ok) {
        throw new Error('Network response was not ok');
      }
  
      const data = await response.json();
      const series = data?.[0];

      // Check if the study was found
      if (series) {
        return series.ID;
      } else {
        return null;
      }
    } catch (error) {
      // Log any errors that occur during the fetch operation
      console.error('There has been a problem with your fetch operation:', error);
      return null;
    }
  };

export default StudyMetadataDisplay;

