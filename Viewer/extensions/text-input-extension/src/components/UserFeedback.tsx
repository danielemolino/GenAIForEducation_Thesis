import React, { useState, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import UserFeedbackRow from './UserFeedbackRow';
import debounce from 'lodash.debounce';

const orthancAuth = `Basic ${window.btoa('orthanc:orthanc')}`;

const orthancFetch = async ({ path, method = 'GET', body = null, contentType = 'text/plain' }) => {
  const attempts = [
    { withAuth: true },
    { withAuth: false },
  ];

  let lastResponse = null;
  for (const attempt of attempts) {
    const headers = { 'Content-Type': contentType };
    if (attempt.withAuth) {
      headers.Authorization = orthancAuth;
    }

    try {
      const response = await fetch(`/pacs${path}`, {
        method,
        headers,
        body,
      });
      lastResponse = response;
      if ([401, 403].includes(response.status)) {
        continue;
      }
      return response;
    } catch (error) {
      // try next auth mode
    }
  }

  return lastResponse;
};

const UserFeedback = ({ seriesID }) => {
  const [feedback, setFeedback] = useState({
    'Anatomically realistic?': { thumbsUp: false, thumbsDown: false },
    'Abnormalities of prompt can be seen in image?': { thumbsUp: false, thumbsDown: false },
    'Location of abnormalities align with prompt?': { thumbsUp: false, thumbsDown: false },
    'Magnitude of abnormalities align with prompt?': { thumbsUp: false, thumbsDown: false },
  });

  useEffect(() => {
    const fetchInitialFeedback = async () => {
      const data = await getMetadataOfSeries(seriesID, 'Feedback');
      console.log(data);
      if (data) {
        setFeedback(JSON.parse(data));
      }
    };

    fetchInitialFeedback();
  }, [seriesID]);

  const handleThumbsUp = async (question) => {
    setFeedback((prevFeedback) => {
      const updatedFeedback = {
        ...prevFeedback,
        [question]: {
          thumbsUp: !prevFeedback[question].thumbsUp,
          thumbsDown: prevFeedback[question].thumbsDown,
        },
      };


      const data = JSON.stringify(updatedFeedback);
      debouncedAddMetadataToSeries(seriesID, data, 'Feedback');

      return updatedFeedback;
    });
  };

  const handleThumbsDown = (question) => {
    setFeedback((prevFeedback) => {
      const updatedFeedback = {
        ...prevFeedback,
        [question]: {
          thumbsUp: prevFeedback[question].thumbsUp,
          thumbsDown: !prevFeedback[question].thumbsDown,
        },
      };

      const data = JSON.stringify(updatedFeedback);
      debouncedAddMetadataToSeries(seriesID, data, 'Feedback');

      return updatedFeedback;
    });
  };

  
  const addMetadataToSeries = async (seriesID, data, type) => {
    if (type !== 'Feedback' ) {
        console.error('Invalid metadata type');
        return;
    }
    try {
        const response = await orthancFetch({
            path: `/series/${seriesID}/metadata/${type}`,
            method: 'PUT',
            contentType: 'text/plain',
            body: data
        });

        if (!response.ok) {
            const errorText = await response.text();
            console.log("Response not ok. Status:", response.status, "Response text:", errorText);
            return;
        }

    } catch (error) {
        console.error('There was a problem with your fetch operation:', error);
    }
};

const debouncedAddMetadataToSeries = useCallback(
    debounce((orthancSeriesID, value, type) => {
      addMetadataToSeries(orthancSeriesID, value, type);
    }, 500),
    [] 
  );

const getMetadataOfSeries = async (seriesID, type) => {
    if (type !== 'Feedback' ) {
        console.error('Invalid metadata type');
        return;
    }
    try {
        const response = await orthancFetch({
            path: `/series/${seriesID}/metadata/${type}`,
            method: 'GET',
            contentType: 'text/plain'
        });

        if (!response.ok) {
            const errorText = await response.text();
            console.log("Response not ok. Status:", response.status, "Response text:", errorText);
            return;
        }
        else {
            return response.text();
        }

    } catch (error) {
        console.error('There was a problem with your fetch operation:', error);
    }
}

  return (
    <div>
      <table className="text-gray-500 text-base w-full mt-2">
      
        <tbody >
          {Object.keys(feedback).map((question) => (
            <UserFeedbackRow
              key={question}
              question={question}
              thumbsUp={feedback[question].thumbsUp}
              thumbsDown={feedback[question].thumbsDown}
              onThumbsUp={() => handleThumbsUp(question)}
              onThumbsDown={() => handleThumbsDown(question)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
};

UserFeedback.propTypes = {
  seriesID: PropTypes.string.isRequired,
};

export default UserFeedback;
