# ========================================================= #
import datetime
import json
import os
from enum import Enum
from typing import Union

import httpx
import requests
from httpx import Response as HttpxResponse
from requests.models import Response

# ========================================================= #


TIME_FRAME_CHUNKS = {
    "second": datetime.timedelta(hours=1),
    "minute": datetime.timedelta(days=45),
    "min": datetime.timedelta(days=45),
    "hour": datetime.timedelta(days=60),
    "day": datetime.timedelta(days=3500),
    "week": datetime.timedelta(days=3500),
    "month": datetime.timedelta(days=3500),
    "quarter": datetime.timedelta(days=3500),
    "year": datetime.timedelta(days=3500),
}


# ========================================================= #


# Just a very basic class to house methods which are common to both sync and async clients
class Base:
    def split_date_range(self, start, end, timespan: str, high_volatility: bool = False, reverse: bool = True) -> list:
        """
        Internal helper function to split a BIGGER date range into smaller chunks to be able to easily fetch
        aggregate bars data. The chunks duration is supposed to be different for time spans.
        For 1 minute bars, multiplier would be 1, timespan would be 'minute'

        :param start: start of the time frame. accepts date, datetime objects or a string ``YYYY-MM-DD``
        :param end: end of the time frame. accepts date, datetime objects or a string ``YYYY-MM-DD``
        :param timespan: The frequency type. like day or minute. see :class:`polygon.enums.Timespan` for choices
        :param high_volatility: Specifies whether the symbol/security in question is highly volatile. If set to True,
                                the lib will use a smaller chunk of time to ensure we don't miss any data due to 50k
                                candle limit. Defaults to False.
        :param reverse: If True (the default), will reverse the order of chunks (chronologically)
        :return: a list of tuples. each tuple is in format ``(start, end)`` and represents one chunk of time frame
        """
        # The Time Travel begins
        if timespan == "min":
            timespan = "minute"

        try:
            delta, temp = TIME_FRAME_CHUNKS[timespan], (start, end)
        except KeyError:
            raise ValueError(
                "Invalid timespan. Use a correct enum or a correct value. See "
                "https://polygon.readthedocs.io/en/latest/Library-Interface-Documentation.html#polygon"
                ".enums.Timespan"
            )

        if high_volatility:
            if timespan in ["minute", "hour"]:
                delta = datetime.timedelta(days=delta.days - 20)
            else:
                delta = datetime.timedelta(days=delta.days - 1500)

        start, end = self.normalize_datetime(start), self.normalize_datetime(end, _dir="end")

        start, end = self.normalize_datetime(start, "datetime"), self.normalize_datetime(end, "datetime")

        if (end - start).days < delta.days:
            return [(self.normalize_datetime(temp[0], "nts"), self.normalize_datetime(temp[1], "nts"))]

        final_time_chunks, timespan, current = [], self._change_enum(timespan), start

        while 1:
            probable_next_date = current + delta

            if probable_next_date >= end:
                if current == probable_next_date:
                    break

                final_time_chunks.append((current, end))
                break

            final_time_chunks.append((current, probable_next_date))
            current = probable_next_date

        if reverse:
            final_time_chunks.reverse()

        return final_time_chunks

    @staticmethod
    def normalize_datetime(
        dt, output_type: str = "ts", _dir: str = "start", _format: str = "%Y-%m-%d", unit: str = "ms", tz=None
    ):
        """
        a core method to perform some specific datetime operations before/after interaction with the API

        :param dt: The datetime input
        :param output_type: what to return. defaults to timestamp (utc if unaware obj)
        :param _dir: whether the input is meant for start of a range or end of it
        :param _format: The format string to use IFF expected to return as string
        :param unit: the timestamp units to work with. defaults to ms (milliseconds)
        :param tz: the timezone to assume/use. defaults to None (which means UTC)
        :return: The output timestamp or formatted string
        """
        if not tz:
            tz = datetime.timezone.utc

        if unit == "ms":
            factor = 1000
        elif unit == "ns":
            factor = 1000000000
        else:
            factor = 1

        if isinstance(dt, datetime.datetime):
            if output_type == "date":
                return dt.date()

            dt = dt.replace(tzinfo=tz) if (dt.tzinfo is None) or (dt.tzinfo.utcoffset(dt) is None) else dt

            if output_type == "datetime":
                return dt
            elif output_type in ["ts", "nts"]:
                return int(dt.timestamp() * factor)
            elif output_type == "str":
                return dt.strftime(_format)

        if isinstance(dt, str):
            dt = datetime.datetime.strptime(dt, _format).date()

        if isinstance(dt, datetime.date):
            if output_type == "ts" and _dir == "start":
                return int(datetime.datetime(dt.year, dt.month, dt.day).replace(tzinfo=tz).timestamp() * factor)
            elif output_type == "ts" and _dir == "end":
                return int(datetime.datetime(dt.year, dt.month, dt.day, 23, 59).replace(tzinfo=tz).timestamp() * factor)
            elif output_type in ["str", "nts"]:
                return dt.strftime(_format)
            elif output_type == "datetime":
                return datetime.datetime(dt.year, dt.month, dt.day).replace(tzinfo=tz)
            elif output_type == "date":
                return dt

        elif isinstance(dt, (int, float)):
            if output_type in ["ts", "nts"]:
                return dt

            dt = datetime.datetime.utcfromtimestamp(dt / factor).replace(tzinfo=tz)

            if output_type == "str":
                return dt.strftime(_format)
            elif output_type == "datetime":
                return dt
            elif output_type == "date":
                return dt.date()

    @staticmethod
    def _change_enum(val: Union[str, Enum, float, int], allowed_type=str):
        if isinstance(val, Enum):
            try:
                return val.value

            except AttributeError:
                raise ValueError(
                    f"The value supplied: ({val}) does not match the required type: ({allowed_type}). "
                    f"Please consider using the  specified enum in the docs for this function or recheck "
                    f"the value supplied."
                )

        if isinstance(allowed_type, list):
            if type(val) in allowed_type:
                return val

            raise ValueError(
                f"The value supplied: ({val}) does not match the required type: ({allowed_type}). "
                f"Please consider using the  specified enum in the docs for this function or recheck "
                f"the value supplied."
            )

        if isinstance(val, allowed_type) or val is None:
            return val

    @staticmethod
    def to_json_safe(response: Union[Response, dict]) -> dict:
        if isinstance(response, dict):
            return response
        try:
            return response.json()
        except json.decoder.JSONDecodeError as e:
            return vars(e)

    def get_dates_between(self, from_date=None, to_date=None, include_to_date: bool = True) -> list:
        """
        Get a list of dates between the two specified dates (from_date and to_date)

        :param from_date: The start date
        :param to_date: The end date
        :param include_to_date: Whether to include the end date in the list
        :return: A list of dates between the two specified dates
        """
        if from_date is None or to_date is None:
            return []

        from_date = self.normalize_datetime(from_date, "date")
        to_date = self.normalize_datetime(to_date, "date")

        if from_date > to_date:
            raise ValueError("The start date cannot be after the end date")

        dates, iterator = [], range(int((to_date - from_date).days))

        if include_to_date:
            dates, iterator = [], range(int((to_date - from_date).days) + 1)

        for day in iterator:
            dates.append(from_date + datetime.timedelta(days=day))

        return dates


# ========================================================= #


class BaseClient(Base):
    """
    These docs are not meant for general users. These are library API references. The actual docs will be
    available on the index page when they are prepared.

    This is the **base client class** for all other REST clients which inherit from this class and implement their own
    endpoints on top of it.
    """

    def __init__(self, api_key: str, connect_timeout: int = 10, read_timeout: int = 10):
        """
        Initiates a Client to be used to access all the endpoints.

        :param api_key: Your API Key. Visit your dashboard to get yours.
        :param connect_timeout: The connection timeout in seconds. Defaults to 10. basically the number of seconds to
                                wait for a connection to be established. Raises a ``ConnectTimeout`` if unable to
                                connect within specified time limit.
        :param read_timeout: The read timeout in seconds. Defaults to 10. basically the number of seconds to wait for
                             date to be received. Raises a ``ReadTimeout`` if unable to connect within the specified
                             time limit.
        """
        self.KEY = api_key
        self.BASE = "https://api.polygon.io"

        self.time_out_conf = (connect_timeout, read_timeout)
        self.session = requests.session()

        self.session.headers.update({"Authorization": f"Bearer {self.KEY}"})

    # Context Managers
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()

    def close(self):
        """
        Closes the ``requests.Session`` and frees up resources. It is recommended to call this method in your
        exit handlers
        """

        self.session.close()

    # Internal Functions
    def _get_response(self, path: str, params: dict = None, raw_response: bool = True) -> Union[Response, dict]:
        """
        Get response on a path. Meant to be used internally but can be used if you know what you're doing

        :param path: RESTful path for the endpoint. Available on the docs for the endpoint right above its name.
        :param params: Query Parameters to be supplied with the request. These are mapped 1:1 with the endpoint.
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to check the
                             status code or inspect the headers. Defaults to True which returns the ``Response`` object.
        :return: A Response object by default. Make ``raw_response=False`` to get JSON decoded Dictionary
        """
        _res = self.session.request("GET", self.BASE + path, params=params, timeout=self.time_out_conf)

        if raw_response:
            return _res

        return self.to_json_safe(_res)

    def get_page_by_url(self, url: str, raw_response: bool = False) -> Union[Response, dict]:
        """
        Get the next page of a response. The URl is returned within ``next_url`` attribute on endpoints which support
        pagination (e.g. the tickers endpoint). If the response doesn't contain this attribute, either all pages were
        received or the endpoint doesn't have pagination. Meant for internal use primarily.

        :param url: The next URL. As contained in ``next_url`` of the response.
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: Either a Dictionary or a Response object depending on value of raw_response. Defaults to Dict.
        """
        _res = self.session.request("GET", url)

        if raw_response:
            return _res

        return self.to_json_safe(_res)

    def get_next_page(
        self, old_response: Union[Response, dict], raw_response: bool = False
    ) -> Union[Response, dict, bool]:
        """
        Get the next page using the most recent old response. This function simply parses the next_url attribute
        from the  existing response and uses it to get the next page. Returns False if there is no next page
        remaining (which implies that you have reached the end of all pages or the endpoint doesn't support pagination).

        :param old_response: The most recent existing response. Can be either ``Response`` Object or Dictionaries
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: A JSON decoded Dictionary by default. Make ``raw_response=True`` to get underlying response object
        """

        try:
            if not isinstance(old_response, (dict, list)):
                old_response = old_response.json()

            _next_url = old_response["next_url"]

            return self.get_page_by_url(_next_url, raw_response=raw_response)

        except KeyError:
            return False

    def get_previous_page(
        self, old_response: Union[Response, dict], raw_response: bool = False
    ) -> Union[Response, dict, bool]:
        """
        Get the previous page using the most recent old response. This function simply parses the previous_url attribute
        from the  existing response and uses it to get the previous page. Returns False if there is no previous page
        remaining (which implies that you have reached the start of all pages or the endpoint doesn't support
        pagination).

        :param old_response: The most recent existing response. Can be either ``Response`` Object or Dictionaries
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: A JSON decoded Dictionary by default. Make ``raw_response=True`` to get underlying response object
        """

        try:
            if not isinstance(old_response, (dict, list)):
                old_response = old_response.json()

            _prev_url = old_response["previous_url"]

            return self.get_page_by_url(_prev_url, raw_response=raw_response)

        except KeyError:
            return False

    def get_all_pages(
        self,
        old_response,
        max_pages: int = None,
        direction: str = "next",
        verbose: bool = False,
        raw_responses: bool = False,
    ):
        """
        A helper function for endpoints which implement pagination using ``next_url`` and ``previous_url`` attributes.
        Can be used externally too to get all responses in a list.

        :param old_response: The last response you had. In most cases, this would be simply the very first response.
        :param max_pages: If you want to limit the number of pages to retrieve. Defaults to None which fetches ALL
                          available pages
        :param direction: The direction to paginate in. Defaults to next which grabs all next_pages. see
                          :class:`polygon.enums.PaginationDirection` for choices
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_responses: If set to True, the elements in container list, you will get underlying Response object
                              instead of the json formatted dict/list. Only use if you need to check status codes or
                              headers. Defaults to False, which makes it return decoded data in list.
        :return: A list of responses. By default, responses are actual json decoded dict/list. Depending on value of
                 ``raw_response``
        """

        direction, container, _res = self._change_enum(direction, str), [], old_response
        if not max_pages:
            if verbose:
                print(f"No max limit specified. Initiating pagination for ALL available pages...")
            max_pages = float("inf")

        if direction in ["prev", "previous"]:
            fn = self.get_previous_page
        else:
            fn = self.get_next_page

        # Start paginate
        while 1:
            if len(container) >= max_pages:
                if verbose:
                    print(f"Max number of pages ({max_pages}) reached. Stopping and aggregating results...")
                break

            _res = fn(_res, raw_response=True)

            if not _res:
                if verbose:
                    print(f"No more pages remain. Stopping and aggregating results...")
                break

            if verbose:
                print(f"Fetched another page... total pages so far: {len(container)}")

            if raw_responses:
                container.append(_res)
                continue

            container.append(self.to_json_safe(_res))

        return container

    def _paginate(
        self,
        _res,
        merge_all_pages: bool = True,
        max_pages: int = None,
        verbose: bool = False,
        raw_page_responses: bool = False,
    ):
        """
        Internal function to call the core pagination methods to build the response object to be parsed by individual
        methods.

        :param merge_all_pages: whether to merge all the pages into one response. defaults to True
        :param max_pages: number of pages to fetch. defaults to all available pages.
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: whether to keep raw response objects or decode them. Only considered if
                                   merge_all_pages is set to False. Defaults to False.
        :return:
        """

        if isinstance(max_pages, int):
            max_pages -= 1

        # How many pages do you want?? YES!!!
        if merge_all_pages:  # prepare for a merge
            pages = [self.to_json_safe(_res)] + self.get_all_pages(_res, max_pages=max_pages, verbose=verbose)
        elif raw_page_responses:  # we don't need your help, adventurer (no merge, no decoding)
            return [_res] + self.get_all_pages(_res, raw_responses=True, max_pages=max_pages, verbose=verbose)
        else:  # okay a little bit of help is fine  (no merge, only decoding)
            return [self.to_json_safe(_res)] + self.get_all_pages(_res, max_pages=max_pages, verbose=verbose)

        # We need your help adventurer  (decode and merge)
        container = []
        try:
            for page in pages:
                container += page["results"]
        except KeyError:
            return pages

        return container

    def get_full_range_aggregates(
        self,
        fn,
        symbol: str,
        time_chunks: list,
        run_parallel: bool = True,
        max_concurrent_workers: int = os.cpu_count() * 5,
        warnings: bool = True,
        info: bool = True,
        adjusted: bool = True,
        sort="asc",
        limit: int = 5000,
        multiplier: int = 1,
        timespan="day",
    ) -> list:
        """
        Internal helper function to fetch aggregate bars for BIGGER time ranges. Should only be used internally.
        Users should prefer the relevant aggregate function with additional parameters.

        :param fn: The method to call in each chunked timeframe
        :param symbol: The ticker symbol to get data for
        :param time_chunks: The list of time chunks as returned by method ``split_datetime_range``
        :param run_parallel: If true (the default), it will use an internal ``ThreadPool`` to get the responses in
                             parallel. **Note That** since python has the GIL restrictions, it would mean that if you
                             have a ThreadPool of your own, only one ThreadPool will be running at a time and the
                             other pool will wait. set to False to get all responses in sequence (will take time)
        :param warnings: Defaults to True which prints warnings. Set to False to disable warnings.
        :param info: Defaults to True which prints mild level warnings / informational messages e.g. when
                     there is no data in response but the response is otherwise OK. Set to False to disable
        :param max_concurrent_workers: This is only used if run_parallel is set to true. Controls how many worker
                                       threads are spawned in the internal thread pool. Defaults to ``your cpu core
                                       count * 5``
        :param adjusted: Whether the results are adjusted for splits. By default, results are adjusted. Set this
                         to false to get results that are NOT adjusted for splits.
        :param sort: Sort the results by timestamp. See :class:`polygon.enums.SortOrder` for choices. ``asc`` default.
        :param limit: Limits the number of base aggregates queried to create the aggregate results. Max 50000 and
                      Default 5000.
        :param multiplier: The size of the timespan multiplier. Must be a positive whole number.
        :param timespan: The size of the time window. See :class:`polygon.enums.Timespan` for choices. defaults to
                         ``day``
        :return: A single merged list of ALL candles/bars
        """

        if run_parallel and info:
            print(
                f"WARNING: Running with threading will spawn an internal ThreadPool to get responses in parallel. "
                f"It is fine if you are not running a ThreadPool of your own. But If you are, know that only one "
                f"pool will run at a time due to python GIL restriction. Other pool will wait. You can pass "
                f"warnings=False to disable this warning OR pass run_parallel=False to disable running internal "
                f"thread pool"
            )
        if (not run_parallel) and info:
            print(
                f"WARNING: Running sequentially can take a lot of time especially if you are pulling minute/hour "
                f"aggs on a BIG time frame. If you have more than one symbol to run, it is suggested to run both "
                f"of them in their own thread. You can pass warnings=False to disable this warning OR "
                f"pass run_parallel=True to run an internal thread pool if you are not running a thread pool of "
                f"your own"
            )

        # The aggregation begins
        dupe_handler, final_results = 0, []

        if run_parallel:
            from concurrent.futures import ThreadPoolExecutor

            sort_order = self._change_enum(sort)
            futures = []

            with ThreadPoolExecutor(max_workers=max_concurrent_workers) as pool:
                for chunk in time_chunks:
                    chunk = (
                        self.normalize_datetime(chunk[0], "nts"),
                        self.normalize_datetime(chunk[1], "nts", _dir="end"),
                    )
                    futures.append(
                        pool.submit(
                            fn,
                            symbol,
                            chunk[0],
                            chunk[1],
                            adjusted=adjusted,
                            sort="asc",
                            limit=500000,
                            multiplier=multiplier,
                            timespan=timespan,
                        )
                    )

            for future in reversed(futures):
                try:
                    data = future.result()["results"]
                except KeyError:
                    if future.result().get("status") == "OK":
                        if info:
                            print(f"INFO: No data returned. response: {future.result()}")
                    elif warnings:
                        print(f"WARN: No data returned. response: {future.result()}")
                    continue

                if len(data) < 1:
                    if future.result().get("status") == "OK":
                        if info:
                            print(f"INFO: No data returned. response: {future.result()}")
                    elif warnings:
                        print(f"WARN: No data returned. response: {future.result()}")
                    continue

                final_results += [candle for candle in data if (candle["t"] > dupe_handler)]
                dupe_handler = final_results[-1]["t"]

            if sort_order in ["desc", "descending"]:
                final_results.reverse()

            return final_results

        # Sequential
        current_dt = self.normalize_datetime(time_chunks[0])
        end_dt = self.normalize_datetime(time_chunks[1], _dir="end")
        first_entry = self.normalize_datetime(time_chunks[0])

        try:
            delta = TIME_FRAME_CHUNKS[timespan]
        except KeyError:
            raise ValueError(
                "Invalid timespan. Use a correct enum or a correct value. See "
                "https://polygon.readthedocs.io/en/latest/Library-Interface-Documentation.html#polygon"
                ".enums.Timespan"
            )

        if (
            self.normalize_datetime(end_dt, "datetime", "end") - self.normalize_datetime(first_entry, "datetime")
        ).days <= delta.days:
            res = fn(
                symbol,
                current_dt,
                end_dt,
                adjusted=adjusted,
                sort=sort,
                limit=500000,
                multiplier=multiplier,
                timespan=timespan,
                full_range=False,
            )
            try:
                return res["results"]
            except KeyError:
                if res.get("status") == "OK":
                    if info:
                        print(
                            f"INFO: no data returned for {symbol} for range {first_entry} to {end_dt}. Response: "
                            f"{res}"
                        )
                elif warnings:
                    print(
                        f"WARN: no data returned for {symbol} for range {first_entry} to {end_dt}. Response: " f"{res}"
                    )
                return []

        dupe_handler = current_dt

        while 1:
            if current_dt >= end_dt:
                break

            res = fn(
                symbol,
                current_dt,
                end_dt,
                adjusted=adjusted,
                sort=sort,
                limit=500000,
                multiplier=multiplier,
                timespan=timespan,
                full_range=False,
            )

            try:
                data = res["results"]
            except KeyError:
                if res.get("status") == "OK":
                    if info:
                        print(
                            f"INFO: No data found for {symbol} between "
                            f"{datetime.datetime.fromtimestamp(current_dt/1e3)} and "
                            f"{datetime.datetime.fromtimestamp(end_dt/1e3)} with response: {res}."
                            f" Terminating loop..."
                        )
                elif warnings:
                    print(
                        f"WARN: No data found for {symbol} between {datetime.datetime.fromtimestamp(current_dt/1e3)}"
                        f" and {datetime.datetime.fromtimestamp(end_dt/1e3)} with response: {res}. "
                        f"Terminating loop..."
                    )
                break

            if len(data) < 1:
                if warnings:
                    print(
                        f"No data found for {symbol} between {datetime.datetime.fromtimestamp(current_dt/1e3)} and "
                        f"{datetime.datetime.fromtimestamp(end_dt/1e3)} with response: {res}. Terminating loop..."
                    )
                break

            temp_len = len(final_results)

            final_results += [candle for candle in data if (candle["t"] > dupe_handler)]

            if len(final_results) == temp_len:
                if data[-1]["t"] <= dupe_handler:
                    break

            current_dt = final_results[-1]["t"]
            dupe_handler = current_dt

        return final_results

    # Technical Indicators
    def _get_sma(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        window_size: int = 50,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Simple Moving Average. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate the simple moving average are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param window_size: The window size used to calculate the simple moving average (SMA). i.e. a window
                            size of 10 with daily aggregates would result in a 10-day moving average.
        :param series_type: The prices in the aggregate which will be used to calculate the SMA.
                            The default ``close`` will result in using close prices to calculate the SMA.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns the SMA.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/sma/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "window": window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
        }

        res = self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)

    def _get_ema(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        window_size: int = 50,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Exponential Moving Average. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate the simple moving average are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param window_size: The window size used to calculate the EMA. i.e. a window
                            size of 10 with daily aggregates would result in a 10-day moving average.
        :param series_type: The prices in the aggregate which will be used to calculate the SMA.
                            The default ``close`` will result in using close prices to calculate the EMA.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns the EMA.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/ema/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "window": window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
        }

        res = self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)

    def _get_rsi(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        window_size: int = 14,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Relative Strength Index. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate RSI are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param window_size: The window size used to calculate RSI. i.e. a window
                            size of 14 with daily aggregates would result in a 14 day RSI.
        :param series_type: The prices in the aggregate which will be used to calculate RSI.
                            The default ``close`` will result in using close prices to calculate RSI.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns RSI.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/rsi/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "window": window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
        }

        res = self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)

    def _get_macd(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        long_window_size: int = 50,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        short_window_size: int = 50,
        signal_window_size: int = 50,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Moving Average Convergence/Divergence. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate the MACD are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param long_window_size: The long window size used to calculate the MACD data
        :param series_type: The prices in the aggregate which will be used to calculate the MACD.
                            The default ``close`` will result in using close prices to calculate the MACD.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns the MACD.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param short_window_size: The short window size used to calculate the MACD data
        :param signal_window_size: The window size used to calculate the MACD signal line.
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/macd/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "long_window": long_window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
            "short_window": short_window_size,
            "signal_window": signal_window_size,
        }

        res = self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)


# ========================================================= #


class BaseAsyncClient(Base):
    """
    These docs are not meant for general users. These are library API references. The actual docs will be
    available on the index page when they are prepared.

    This is the **base async client class** for all other REST clients which inherit from this class and implement
    their own endpoints on top of it.
    """

    def __init__(
        self,
        api_key: str,
        connect_timeout: int = 10,
        read_timeout: int = 10,
        pool_timeout: int = 10,
        max_connections: int = None,
        max_keepalive: int = None,
        write_timeout: int = 10,
    ):
        """
        Initiates a Client to be used to access all the endpoints.

        :param api_key: Your API Key. Visit your dashboard to get yours.
        :param connect_timeout: The connection timeout in seconds. Defaults to 10. basically the number of seconds to
                                wait for a connection to be established. Raises a ``ConnectTimeout`` if unable to
                                connect within specified time limit.
        :param read_timeout: The read timeout in seconds. Defaults to 10. basically the number of seconds to wait for
                             data to be received. Raises a ``ReadTimeout`` if unable to connect within the specified
                             time limit.
        :param pool_timeout: The pool timeout in seconds. Defaults to 10. Basically the number of seconds to wait while
                             trying to get a connection from connection pool. Do NOT change if you're unsure of what it
                             implies
        :param max_connections: Max number of connections in the pool. Defaults to NO LIMITS. Do NOT change if you're
                                unsure of application
        :param max_keepalive: max number of allowable keep alive connections in the pool. Defaults to no limit.
                              Do NOT change if you're unsure of the applications.
        :param write_timeout: The write timeout in seconds. Defaults to 10. basically the number of seconds to wait for
                             data to be written/posted. Raises a ``WriteTimeout`` if unable to connect within the
                             specified time limit.
        """
        self.KEY = api_key
        self.BASE = "https://api.polygon.io"

        self.time_out_conf = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, pool=pool_timeout, write=write_timeout
        )
        self._conn_pool_limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_keepalive)
        self.session = httpx.AsyncClient(timeout=self.time_out_conf, limits=self._conn_pool_limits)

        self.session.headers.update({"Authorization": f"Bearer {self.KEY}"})

    @staticmethod
    async def aw_task(aw, semaphore):
        async with semaphore:
            return await aw

    # Context Managers
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.aclose()

    async def close(self):
        """
        Closes the ``httpx.AsyncClient`` and frees up resources. It is recommended to call this method in your
        exit handlers. This method should be awaited as this is a coroutine.
        """

        await self.session.aclose()

    # Internal Functions
    async def _get_response(
        self, path: str, params: dict = None, raw_response: bool = True
    ) -> Union[HttpxResponse, dict]:
        """
        Get response on a path - meant to be used internally but can be used if you know what you're doing

        :param path: RESTful path for the endpoint. Available on the docs for the endpoint right above its name.
        :param params: Query Parameters to be supplied with the request. These are mapped 1:1 with the endpoint.
        :param raw_response: whether to return the ``Response`` Object. Useful for when you need to check the
                             status code or inspect the headers. Defaults to True which returns the ``Response`` object.
        :return: A Response object by default. Make ``raw_response=False`` to get JSON decoded Dictionary
        """
        _res = await self.session.request(
            "GET", self.BASE + path, params={key: value for key, value in params.items() if value} if params else None
        )

        if raw_response:
            return _res

        return self.to_json_safe(_res)

    async def get_page_by_url(self, url: str, raw_response: bool = False) -> Union[HttpxResponse, dict]:
        """
        Get the next page of a response. The URl is returned within ``next_url`` attribute on endpoints which support
        pagination (e.g. the tickers' endpoint). If the response doesn't contain this attribute, either all pages were
        received or the endpoint doesn't have pagination. Meant for internal use primarily.

        :param url: The next URL. As contained in ``next_url`` of the response.
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: Either a Dictionary or a Response object depending on value of raw_response. Defaults to Dict.
        """
        _res = await self.session.request("GET", url)

        if raw_response:
            return _res

        return self.to_json_safe(_res)

    async def get_next_page(
        self, old_response: Union[HttpxResponse, dict], raw_response: bool = False
    ) -> Union[HttpxResponse, dict, bool]:
        """
        Get the next page using the most recent old response. This function simply parses the next_url attribute
        from the  existing response and uses it to get the next page. Returns False if there is no next page
        remaining (which implies that you have reached the end of all pages or the endpoint doesn't support
        pagination) - Async method

        :param old_response: The most recent existing response. Can be either ``Response`` Object or Dictionaries
        :param raw_response: whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: A JSON decoded Dictionary by default. Make ``raw_response=True`` to get underlying response object
        """

        try:
            if not isinstance(old_response, dict):
                old_response = old_response.json()

            _next_url = old_response["next_url"]

            return await self.get_page_by_url(_next_url, raw_response=raw_response)

        except KeyError:
            return False

    async def get_previous_page(
        self, old_response: Union[HttpxResponse, dict], raw_response: bool = False
    ) -> Union[HttpxResponse, dict, bool]:
        """
        Get the previous page using the most recent old response. This function simply parses the previous_url attribute
        from the  existing response and uses it to get the previous page. Returns False if there is no previous page
        remaining (which implies that you have reached the start of all pages or the endpoint doesn't support
        pagination) - Async method

        :param old_response: The most recent existing response. Can be either ``Response`` Object or Dictionaries
        :param raw_response: whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: A JSON decoded Dictionary by default. Make ``raw_response=True`` to get underlying response object
        """

        try:
            if not isinstance(old_response, dict):
                old_response = old_response.json()

            _prev_url = old_response["previous_url"]

            return await self.get_page_by_url(_prev_url, raw_response=raw_response)

        except KeyError:
            return False

    async def get_all_pages(
        self,
        old_response,
        max_pages: int = None,
        direction: str = "next",
        verbose: bool = False,
        raw_responses: bool = False,
    ):
        """
        A helper function for endpoints which implement pagination using ``next_url`` and ``previous_url`` attributes.
        Can be used externally too to get all responses in a list.

        :param old_response: The last response you had. In most cases, this would be simply the very first response.
        :param max_pages: If you want to limit the number of pages to retrieve. Defaults to None which fetches ALL
                          available pages
        :param direction: The direction to paginate in. Defaults to next which grabs all next_pages. see
                          :class:`polygon.enums.PaginationDirection` for choices
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_responses: If set to True, the elements in container list, you will get underlying Response object
                              instead of the json formatted dict/list. Only use if you need to check status codes or
                              headers. Defaults to False, which makes it return decoded data in list.
        :return: A list of responses. By default, responses are actual json decoded dict/list. Depending on value of
                 ``raw_response``
        """

        direction, container, _res = self._change_enum(direction, str), [], old_response
        if not max_pages:
            if verbose:
                print(f"No max limit specified. Initiating pagination for ALL available pages...")
            max_pages = float("inf")

        if direction in ["prev", "previous"]:
            fn = self.get_previous_page
        else:
            fn = self.get_next_page

        # Start paginate
        while 1:
            if len(container) >= max_pages:
                if verbose:
                    print(f"Max number of pages ({max_pages}) reached. Stopping and aggregating results...")
                break

            _res = await fn(_res, raw_response=True)

            if not _res:
                if verbose:
                    print(f"No more pages remain. Stopping and aggregating results...")
                break

            if verbose:
                print(f"Fetched another page... total pages so far: {len(container)}")

            if raw_responses:
                container.append(_res)
                continue

            container.append(self.to_json_safe(_res))

        return container

    async def _paginate(
        self,
        _res,
        merge_all_pages: bool = True,
        max_pages: int = None,
        verbose: bool = False,
        raw_page_responses: bool = False,
    ):
        """
        Internal function to call the core pagination methods to build the response object to be parsed by individual
        methods.

        :param merge_all_pages: whether to merge all the pages into one response. defaults to True
        :param max_pages: number of pages to fetch. defaults to all available pages.
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: whether to keep raw response objects or decode them. Only considered if
                                   merge_all_pages is set to False. Defaults to False.
        :return:
        """

        if isinstance(max_pages, int):
            max_pages -= 1

        # How many pages do you want?? YES!!!
        if merge_all_pages:  # prepare for a merge
            pages = [self.to_json_safe(_res)] + await self.get_all_pages(_res, max_pages=max_pages, verbose=verbose)
        elif raw_page_responses:  # we don't need your help, adventurer (no merge, no decoding)
            return [_res] + await self.get_all_pages(_res, raw_responses=True, max_pages=max_pages, verbose=verbose)
        else:  # okay a little bit of help is fine  (no merge, only decoding)
            return [self.to_json_safe(_res)] + await self.get_all_pages(_res, max_pages=max_pages, verbose=verbose)

        # We need your help adventurer  (decode and merge)
        container = []
        try:
            for page in pages:
                container += page["results"]
        except KeyError:
            return pages

        return container

    async def get_full_range_aggregates(
        self,
        fn,
        symbol: str,
        time_chunks: list,
        run_parallel: bool = True,
        max_concurrent_workers: int = os.cpu_count() * 5,
        warnings: bool = True,
        info: bool = True,
        adjusted: bool = True,
        sort="asc",
        limit: int = 5000,
        multiplier: int = 1,
        timespan="day",
    ) -> list:
        """
        Internal helper function to fetch aggregate bars for BIGGER time ranges. Should only be used internally.
        Users should prefer the relevant aggregate function with additional parameters.

        :param fn: The method to call in each chunked timeframe
        :param symbol: The ticker symbol to get data for
        :param time_chunks: The list of time chunks as returned by method ``split_datetime_range``
        :param run_parallel: If true (the default), it will use an internal ``ThreadPool`` to get the responses in
                             parallel. **Note That** since python has the GIL restrictions, it would mean that if you
                             have a ThreadPool of your own, only one ThreadPool will be running at a time and the
                             other pool will wait. set to False to get all responses in sequence (will take time)
        :param warnings: Defaults to True which prints warnings. Set to False to disable warnings.
        :param info: Defaults to True which prints mild warnings and informational messages. E.g. if the
                     response came back with no data, but otherwise it was a valid response
        :param max_concurrent_workers: This is only used if run_parallel is set to true. Controls how many worker
                                       coroutines are spawned internally. Defaults to ``your cpu core count * 5``.
                                       An ``asyncio.Semaphore()`` is used behind the scenes.
        :param adjusted: whether the results are adjusted for splits. By default, results are adjusted. Set this
                         to false to get results that are NOT adjusted for splits.
        :param sort: Sort the results by timestamp. See :class:`polygon.enums.SortOrder` for choices. ``asc`` default.
        :param limit: Limits the number of base aggregates queried to create the aggregate results. Max 50000 and
                      Default 5000.
        :param multiplier: The size of the timespan multiplier. Must be a positive whole number.
        :param timespan: The size of the time window. See :class:`polygon.enums.Timespan` for choices. defaults to
                         ``day``
        :return: A single merged list of ALL candles/bars
        """

        if (not run_parallel) and info:
            print(
                f"WARNING: Running sequentially can take a lot of time especially if you are pulling minute/hour "
                f"aggs on a BIG time frame. If you have more than one symbols to run, it is suggested to run one "
                f"coroutine for each ticker. You can pass warnings=False to disable this warning OR "
                f"pass run_parallel=True to spawn internal coroutines to get data in parallel"
            )

        # The aggregation begins
        dupe_handler, final_results = 0, []

        if run_parallel:
            import asyncio

            sort_order = self._change_enum(sort)
            futures, semaphore = [], asyncio.Semaphore(max_concurrent_workers)

            for chunk in time_chunks:
                chunk = (self.normalize_datetime(chunk[0], "nts"), self.normalize_datetime(chunk[1], "nts", _dir="end"))

                futures.append(
                    self.aw_task(
                        fn(
                            symbol,
                            chunk[0],
                            chunk[1],
                            adjusted=adjusted,
                            sort="asc",
                            limit=500000,
                            multiplier=multiplier,
                            timespan=timespan,
                            full_range=False,
                        ),
                        semaphore,
                    )
                )

            futures = await asyncio.gather(*futures)

            for future in reversed(futures):
                try:
                    data = future["results"]
                except KeyError:
                    if future.get("status") == "OK":
                        if info:
                            print(f"INFO: No data returned. Response: {future}")
                    elif warnings:
                        print(f"WARN: No data returned. Response: {future}")
                    continue

                if len(data) < 1:
                    if future.get("status") == "OK":
                        if info:
                            print(f"INFO: No data returned. Response: {future}")
                    elif warnings:
                        print(f"WARN: No data returned. Response: {future}")
                    continue

                # final_results += [candle for candle in data if (candle['t'] > dupe_handler) and (
                #         candle['t'] <= last_entry) and (candle['t'] >= first_entry)]
                final_results += [candle for candle in data if (candle["t"] > dupe_handler)]
                dupe_handler = final_results[-1]["t"]

            if sort_order in ["desc", "descending"]:
                final_results.reverse()

            return final_results

        # Sequential
        current_dt = self.normalize_datetime(time_chunks[0])
        end_dt = self.normalize_datetime(time_chunks[1], _dir="end")
        first_entry = self.normalize_datetime(time_chunks[0])

        try:
            delta = TIME_FRAME_CHUNKS[timespan]
        except KeyError:
            raise ValueError(
                "Invalid timespan. Use a correct enum or a correct value. See "
                "https://polygon.readthedocs.io/en/latest/Library-Interface-Documentation.html#polygon"
                ".enums.Timespan"
            )

        if (
            self.normalize_datetime(end_dt, "datetime", "end") - self.normalize_datetime(first_entry, "datetime")
        ).days <= delta.days:
            res = await fn(
                symbol,
                current_dt,
                end_dt,
                adjusted=adjusted,
                sort=sort,
                limit=500000,
                multiplier=multiplier,
                timespan=timespan,
                full_range=False,
            )
            try:
                return res["results"]
            except KeyError:
                if res.get("status") == "OK":
                    if info:
                        print(
                            f"INFO: no data returned for {symbol} for range {first_entry} to {end_dt}. Response: "
                            f"{res}"
                        )
                elif warnings:
                    print(
                        f"WARN: no data returned for {symbol} for range {first_entry} to {end_dt}. Response: " f"{res}"
                    )
                return []

        dupe_handler = current_dt

        while 1:
            if current_dt >= end_dt:
                break

            res = await fn(
                symbol,
                current_dt,
                end_dt,
                adjusted=adjusted,
                sort=sort,
                limit=500000,
                multiplier=multiplier,
                timespan=timespan,
                full_range=False,
            )

            try:
                data = res["results"]
            except KeyError:
                if res.get("status") == "OK":
                    if info:
                        print(
                            f"INFO: No data found for {symbol} between "
                            f"{datetime.datetime.fromtimestamp(current_dt/1e3)} and "
                            f"{datetime.datetime.fromtimestamp(end_dt/1e3)} with response: {res}. "
                            f"Terminating loop..."
                        )
                elif warnings:
                    print(
                        f"WARN: No data found for {symbol} between {datetime.datetime.fromtimestamp(current_dt/1e3)}"
                        f" and {datetime.datetime.fromtimestamp(end_dt/1e3)} with response: {res}. "
                        f"Terminating loop..."
                    )
                break

            if len(data) < 1:
                if warnings:
                    print(
                        f"No data found for {symbol} between {datetime.datetime.fromtimestamp(current_dt/1e3)} "
                        f"and {datetime.datetime.fromtimestamp(end_dt/1e3)} with response: {res}. Terminating loop..."
                    )
                break

            temp_len = len(final_results)

            final_results += [candle for candle in data if (candle["t"] > dupe_handler)]

            if len(final_results) == temp_len:
                if data[-1]["t"] <= dupe_handler:
                    break

            current_dt = final_results[-1]["t"]
            dupe_handler = current_dt

        return final_results

    # Technical Indicators
    async def _get_sma(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        window_size: int = 50,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Simple Moving Average. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate the simple moving average are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param window_size: The window size used to calculate the simple moving average (SMA). i.e. a window
                            size of 10 with daily aggregates would result in a 10 day moving average.
        :param series_type: The prices in the aggregate which will be used to calculate the SMA.
                            The default ``close`` will result in using close prices to calculate the SMA.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns the SMA.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/sma/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "window": window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
        }

        res = await self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)

    async def _get_ema(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        window_size: int = 50,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Exponential Moving Average. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate the simple moving average are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param window_size: The window size used to calculate the EMA. i.e. a window
                            size of 10 with daily aggregates would result in a 10 day moving average.
        :param series_type: The prices in the aggregate which will be used to calculate the SMA.
                            The default ``close`` will result in using close prices to calculate the EMA.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns the EMA.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/ema/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "window": window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
        }

        res = await self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)

    async def _get_rsi(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        window_size: int = 14,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Relative Strength Index. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate RSI are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param window_size: The window size used to calculate RSI. i.e. a window
                            size of 14 with daily aggregates would result in a 14 day RSI.
        :param series_type: The prices in the aggregate which will be used to calculate RSI.
                            The default ``close`` will result in using close prices to calculate RSI.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns RSI.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/rsi/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "window": window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
        }

        res = await self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)

    async def _get_macd(
        self,
        symbol: str,
        timestamp=None,
        timespan="day",
        adjusted: bool = True,
        long_window_size: int = 50,
        series_type="close",
        include_underlying: bool = False,
        order="desc",
        limit: int = 5000,
        timestamp_lt=None,
        timestamp_lte=None,
        timestamp_gt=None,
        timestamp_gte=None,
        short_window_size: int = 50,
        signal_window_size: int = 50,
        all_pages: bool = False,
        max_pages: int = None,
        merge_all_pages: bool = True,
        verbose: bool = False,
        raw_page_responses: bool = False,
        raw_response: bool = False,
    ):
        """
        Get the Moving Average Convergence/Divergence. COMMON method for all clients

        :param symbol: The corrected symbol according to asset type
        :param timestamp: Either a date with the format ``YYYY-MM-DD`` or a millisecond timestamp.
        :param timespan: Size of the aggregate time window. defaults to 'day'. See :class:`polygon.enums.Timespan`
                         for choices
        :param adjusted: Whether the aggregates used to calculate the MACD are adjusted for
                         splits. By default, aggregates are adjusted. Set this to ``False`` to get results that are NOT
                         adjusted for splits.
        :param long_window_size: The long window size used to calculate the MACD data
        :param series_type: The prices in the aggregate which will be used to calculate the MACD.
                            The default ``close`` will result in using close prices to calculate the MACD.
                            See :class:`polygon.enums.SeriesType` for choices
        :param include_underlying: Whether to include the OCHLV aggregates used to calculate this
                                   indicator in the response. Defaults to False which only returns the MACD.
        :param order: The order in which to return the results, ordered by timestamp.
                      See :class:`polygon.enums.SortOrder` for choices. Defaults to Descending (most recent first)
        :param limit: Limit the number of results returned, default is 5000 which is also the max
        :param timestamp_lt: Only use results where timestamp is less than supplied value
        :param timestamp_lte: Only use results where timestamp is less than or equal to supplied value
        :param timestamp_gt: Only use results where timestamp is greater than supplied value
        :param timestamp_gte: Only use results where timestamp is greater than or equal to supplied value
        :param short_window_size: The short window size used to calculate the MACD data
        :param signal_window_size: The window size used to calculate the MACD signal line.
        :param all_pages: Whether to paginate through next/previous pages internally. Defaults to False. If set to True,
                          it will try to paginate through all pages and merge all pages internally for you.
        :param max_pages: how many pages to fetch. Defaults to None which fetches all available pages. Change to an
                          integer to fetch at most that many pages. This param is only considered if ``all_pages``
                          is set to True
        :param merge_all_pages: If this is True, returns a single merged response having all the data. If False,
                                returns a list of all pages received. The list can be either a list of response
                                objects or decoded data itself, controlled by parameter ``raw_page_responses``.
                                This argument is Only considered if ``all_pages`` is set to True. Default: True
        :param verbose: Set to True to print status messages during the pagination process. Defaults to False.
        :param raw_page_responses: If this is true, the list of pages will be a list of corresponding Response objects.
                                   Else, it will be a list of actual data for pages. This parameter is only
                                   considered if ``merge_all_pages`` is set to False. Default: False
        :param raw_response: Whether to return the ``Response`` Object. Useful for when you need to say check the
                             status code or inspect the headers. Defaults to False which returns the json decoded
                             dictionary.
        :return: The response object
        """
        _path = f"/v1/indicators/macd/{symbol.upper()}"

        timestamp = self.normalize_datetime(timestamp, "nts")
        timestamp_lt = self.normalize_datetime(timestamp_lt, "nts")
        timestamp_lte = self.normalize_datetime(timestamp_lte, "nts")
        timestamp_gt = self.normalize_datetime(timestamp_gt, "nts")
        timestamp_gte = self.normalize_datetime(timestamp_gte, "nts")

        timespan = self._change_enum(timespan)
        series_type = self._change_enum(series_type)
        order = self._change_enum(order)

        _data = {
            "timestamp": timestamp,
            "timestamp.lt": timestamp_lt,
            "timestamp.lte": timestamp_lte,
            "timestamp.gt": timestamp_gt,
            "timestamp.gte": timestamp_gte,
            "timespan": timespan,
            "adjusted": adjusted,
            "long_window": long_window_size,
            "series_type": series_type,
            "expand_underlying": include_underlying,
            "order": order,
            "limit": limit,
            "short_window": short_window_size,
            "signal_window": signal_window_size,
        }

        res = await self._get_response(_path, params=_data)

        if not all_pages:  # don't you dare paginate!!
            if raw_response:
                return res

            return self.to_json_safe(res)

        return self._paginate(res, merge_all_pages, max_pages, verbose=verbose, raw_page_responses=raw_page_responses)


# ========================================================= #


if __name__ == "__main__":  # Tests
    print("Don't You Dare Running Lib Files Directly")

# ========================================================= #
