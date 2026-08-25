import time
import threading
from typing import Optional


class RateLimiter:
    """
    A simple rate limiter that controls the number of requests per minute.
    Uses a token bucket algorithm with a sliding window approach.
    """
    
    def __init__(self, requests_per_minute: int = 0):
        """
        Initialize the rate limiter.
        
        Args:
            requests_per_minute: Maximum number of requests per minute.
                                If 0, no rate limiting is applied.
        """
        self.requests_per_minute = requests_per_minute
        self.enabled = requests_per_minute > 0
        
        if self.enabled:
            self.requests_per_second = requests_per_minute / 60.0
            self.min_interval = 1.0 / self.requests_per_second if self.requests_per_second > 0 else 0
            self.last_request_time = 0.0
            self._lock = threading.Lock()
    
    def wait_if_needed(self) -> None:
        """
        Wait if necessary to respect the rate limit.
        This method should be called before making each request.
        """
        if not self.enabled:
            return
            
        with self._lock:
            current_time = time.monotonic()
            
            if self.last_request_time > 0:
                time_since_last_request = current_time - self.last_request_time
                
                if time_since_last_request < self.min_interval:
                    sleep_time = self.min_interval - time_since_last_request
                    time.sleep(sleep_time)
                    current_time = time.monotonic()
            
            self.last_request_time = current_time
    
    def get_retry_delay(self) -> float:
        """
        Get the delay to wait before retrying after a 429 error.
        
        Returns:
            Delay in seconds to wait before retrying
        """
        if not self.enabled:
            return 1.0  # Default 1 second delay
        
        # Wait for at least one request slot to be available
        return max(self.min_interval, 1.0)
    
    def is_enabled(self) -> bool:
        """
        Check if rate limiting is enabled.
        
        Returns:
            True if rate limiting is enabled, False otherwise
        """
        return self.enabled
