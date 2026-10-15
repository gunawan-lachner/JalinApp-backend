# JalinApp API Documentation

Complete API documentation for the JalinApp application.

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Core APIs](#core-apis)
4. [Graylog APIs](#graylog-apis)
5. [Transaction APIs](#transaction-apis)
6. [Gateway APIs](#gateway-apis)
7. [Participant APIs](#participant-apis)
8. [Rules APIs](#rules-apis)
9. [Routing APIs](#routing-apis)
10. [SSH APIs](#ssh-apis)
11. [Network Message APIs](#network-message-apis)
12. [Redmine APIs](#redmine-apis)
13. [Query Management APIs](#query-management-apis)
14. [Error Handling](#error-handling)
15. [CORS Support](#cors-support)

---

## Overview

### Base URL

All API endpoints are prefixed with `/api`

### Response Format

All responses are in JSON format with the following structure:

```json
{
  "success": true|false,
  "data": {...},
  "error": "error message",
  "message": "success message"
}
```

### HTTP Status Codes

- `200` - Success
- `201` - Created
- `400` - Bad Request
- `401` - Unauthorized
- `403` - Forbidden
- `404` - Not Found
- `409` - Conflict
- `500` - Internal Server Error

---

## Authentication

### Base Path: `/api/auth`

The API uses JWT (JSON Web Tokens) for authentication. Most endpoints require a valid JWT token in the Authorization header:

```
Authorization: Bearer <token>
```

#### Login

**POST** `/api/auth/login`

Authenticate user and receive JWT token.

**Request Body:**

```json
{
  "username": "string",
  "password": "string"
}
```

**Response:**

```json
{
  "success": true,
  "message": "Login successful",
  "token": "jwt_token_string",
  "user": {
    "id": 1,
    "username": "admin",
    "role_name": "admin",
    "id_role": 1,
    "can_crud": true
  }
}
```

#### Get Current User

**GET** `/api/auth/me`

Get current authenticated user information.

**Headers:**

- `Authorization: Bearer <token>`

**Response:**

```json
{
  "success": true,
  "user": {
    "id": 1,
    "username": "admin",
    "role_name": "admin",
    "id_role": 1,
    "can_crud": true
  }
}
```

#### Get All Users

**GET** `/api/auth/users`

Get list of all users (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Response:**

```json
{
  "success": true,
  "users": [
    {
      "id": 1,
      "username": "admin",
      "role_name": "admin",
      "can_crud": true,
      "last_login": "2024-01-01T00:00:00"
    }
  ]
}
```

#### Create User

**POST** `/api/auth/users`

Create a new user (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Request Body:**

```json
{
  "username": "string",
  "password": "string",
  "id_role": 1
}
```

#### Update User

**PUT** `/api/auth/users/<user_id>`

Update user information (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Request Body:**

```json
{
  "username": "string",
  "password": "string",
  "id_role": 1
}
```

#### Delete User

**DELETE** `/api/auth/users/<user_id>`

Delete a user (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

#### Get All Roles

**GET** `/api/auth/roles`

Get list of all roles (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Response:**

```json
{
  "success": true,
  "roles": [
    {
      "id_role": 1,
      "role_name": "admin",
      "can_crud": true
    }
  ]
}
```

#### Create Role

**POST** `/api/auth/roles`

Create a new role (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Request Body:**

```json
{
  "role_name": "string",
  "can_crud": true
}
```

#### Update Role

**PUT** `/api/auth/roles/<role_id>`

Update role information (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Request Body:**

```json
{
  "role_name": "string",
  "can_crud": true
}
```

#### Delete Role

**DELETE** `/api/auth/roles/<role_id>`

Delete a role (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

#### Get All Pages

**GET** `/api/auth/pages`

Get list of all pages with role assignments (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Response:**

```json
{
  "success": true,
  "pages": [
    {
      "id_page": 1,
      "name": "Dashboard",
      "path": "/dashboard",
      "page_order": 1,
      "icon": "dashboard",
      "role_names": ["admin", "user"],
      "role_ids": [1, 2]
    }
  ]
}
```

#### Get User Pages

**GET** `/api/auth/user-pages`

Get pages accessible to current user.

**Headers:**

- `Authorization: Bearer <token>`

**Response:**

```json
{
  "success": true,
  "pages": [
    {
      "id_page": 1,
      "name": "Dashboard",
      "path": "/dashboard",
      "page_order": 1,
      "icon": "dashboard"
    }
  ]
}
```

#### Get Page Roles

**GET** `/api/auth/pages/<page_id>/roles`

Get roles assigned to a specific page (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

#### Assign Roles to Page

**POST** `/api/auth/pages/<page_id>/roles`

Assign roles to a page (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Request Body:**

```json
{
  "role_ids": [1, 2]
}
```

#### Remove Role from Page

**DELETE** `/api/auth/pages/<page_id>/roles/<role_id>`

Remove a role from a page (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

#### Create Page

**POST** `/api/auth/pages`

Create a new page (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Request Body:**

```json
{
  "name": "string",
  "path": "string",
  "page_order": 0,
  "icon": "string",
  "role_ids": [1, 2]
}
```

#### Update Page

**PUT** `/api/auth/pages/<page_id>`

Update page information (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

**Request Body:**

```json
{
  "name": "string",
  "path": "string",
  "page_order": 0,
  "icon": "string",
  "role_ids": [1, 2]
}
```

#### Delete Page

**DELETE** `/api/auth/pages/<page_id>`

Delete a page (Admin only).

**Headers:**

- `Authorization: Bearer <token>`

---

## Core APIs

### Connections

#### Base Path: `/api/core/connections`

#### List Connection Profiles

**GET** `/api/core/connections/profiles`

Get all connection profiles.

**Response:**

```json
{
  "profiles": [
    {
      "id": 1,
      "name": "Production DB",
      "host": "localhost",
      "port": 3306,
      "user": "user",
      "password": "password",
      "database": "dbname",
      "category": "production",
      "connection_type": "mysql"
    }
  ]
}
```

#### Create Connection Profile

**POST** `/api/core/connections/profiles`

Create a new connection profile.

**Request Body:**

```json
{
  "name": "string",
  "host": "string",
  "port": 3306,
  "user": "string",
  "password": "string",
  "database": "string",
  "category": "string",
  "connection_type": "string"
}
```

#### Update Connection Profile

**PUT** `/api/core/connections/profiles/<profile_name>`

Update an existing connection profile.

**Request Body:**

```json
{
  "host": "string",
  "port": 3306,
  "user": "string",
  "password": "string",
  "database": "string",
  "category": "string",
  "connection_type": "string"
}
```

#### Delete Connection Profile

**DELETE** `/api/core/connections/profiles/<profile_name>`

Delete a connection profile.

#### Test Connection

**POST** `/api/core/connections/test`

Test a database connection.

**Request Body:**

```json
{
  "name": "profile_name"
}
```

### Core Queries

#### Base Path: `/api/core/queries`

#### List Queries

**GET** `/api/core/queries/queries`

Get list of all saved queries.

#### Get Query

**GET** `/api/core/queries/queries/get/<query_id>`

Get a specific query by ID.

#### Get Query by Name

**GET** `/api/core/queries/queries/get_by_name/<query_name>`

Get a query by name.

#### Save Query

**POST** `/api/core/queries/queries/save`

Save a new query.

**Request Body:**

```json
{
  "name": "string",
  "query": "SELECT * FROM table",
  "connection_name": "string",
  "description": "string"
}
```

#### Delete Query

**DELETE** `/api/core/queries/queries/delete/<query_id>`

Delete a query.

#### Execute Query

**POST** `/api/core/queries/queries/execute`

Execute a query.

**Request Body:**

```json
{
  "query": "SELECT * FROM table",
  "connection_name": "string"
}
```

#### List Tables

**POST** `/api/core/queries/queries/list_tables`

Get list of tables for a connection.

**Request Body:**

```json
{
  "connection_name": "string"
}
```

#### Get Table Data

**POST** `/api/core/queries/queries/table_data`

Get data from a table.

**Request Body:**

```json
{
  "connection_name": "string",
  "table_name": "string",
  "limit": 100
}
```

#### Set Connection

**POST** `/api/core/queries/set_connection`

Set the active connection.

**Request Body:**

```json
{
  "connection_name": "string"
}
```

#### Check Table Structure

**GET** `/api/core/queries/check_table_structure`

Check table structure.

#### Fetch Redmine Info

**GET** `/api/core/queries/fetch_redmine_info/<ticket_number>`

Fetch Redmine ticket information.

### Tables

#### Base Path: `/api/table` or `/api/tables`

#### Get Tables

**POST** `/api/table/get_tables`

Get list of tables from a database connection.

**Request Body:**

```json
{
  "connection_name": "string"
}
```

#### Download Table Data

**POST** `/api/table/download`

Download table data as file.

**Request Body:**

```json
{
  "connection_name": "string",
  "table_name": "string",
  "format": "csv|excel|json"
}
```

#### Get Table Columns

**POST** `/api/table/get_table_columns`

Get columns for a specific table.

**Request Body:**

```json
{
  "connection_name": "string",
  "table_name": "string"
}
```

#### Get Source Tables

**POST** `/api/table/get_source_tables`

Get source tables for comparison.

**Request Body:**

```json
{
  "connection_name": "string"
}
```

#### Get Target Tables

**POST** `/api/table/get_target_tables`

Get target tables for comparison.

**Request Body:**

```json
{
  "connection_name": "string"
}
```

#### Compare Tables

**POST** `/api/table/compare_tables`

Compare two tables.

**Request Body:**

```json
{
  "source_connection": "string",
  "source_table": "string",
  "target_connection": "string",
  "target_table": "string"
}
```

#### Compare Tables by Query

**POST** `/api/table/compare_tables_by_query`

Compare tables using custom queries.

**Request Body:**

```json
{
  "source_connection": "string",
  "source_query": "SELECT * FROM table1",
  "target_connection": "string",
  "target_query": "SELECT * FROM table2"
}
```

---

## Graylog APIs

### Graylog Data

#### Base Path: `/api/graylog/data`

#### Get Graylog Data (GET)

**GET** `/api/graylog/data`

Get Graylog messages for a specific UUID.

**Query Parameters:**

- `uuid` (required) - UUID to search for
- `connection` (optional, default: "LOKAL") - Connection name
- `days_back` (optional, default: 2) - Number of days to look back
- `limit` (optional, default: 100) - Maximum number of messages
- `include_rules` (optional, default: "true") - Include rule matching

**Example:**

```
GET /api/graylog/data?uuid=123e4567-e89b-12d3-a456-426614174000&days_back=7&limit=50
```

**Response:**

```json
{
  "success": true,
  "data": {
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "connection": "LOKAL",
    "rules": [],
    "time_range": {
      "from": "2024-01-01T00:00:00.000Z",
      "to": "2024-01-08T00:00:00.000Z",
      "days_back": 7
    },
    "total_messages": 10,
    "messages": [...],
    "successful_queries": ["UUID:\"123e4567-e89b-12d3-a456-426614174000\""],
    "search_info": {
      "queries_tried": [...],
      "successful_queries": [...],
      "total_raw_messages": 10,
      "total_unique_messages": 10,
      "rules_applied": true,
      "total_rules_loaded": 5,
      "messages_with_rules": 3
    }
  }
}
```

#### Get Graylog Data (POST)

**POST** `/api/graylog/data`

Get Graylog messages for a specific UUID using POST method.

**Request Body:**

```json
{
  "uuid": "123e4567-e89b-12d3-a456-426614174000",
  "connection": "LOKAL",
  "days_back": 2,
  "limit": 100,
  "include_rules": true
}
```

#### Batch Get Graylog Data

**POST** `/api/graylog/data/batch`

Get Graylog data for multiple UUIDs.

**Request Body:**

```json
{
  "uuids": [
    "123e4567-e89b-12d3-a456-426614174000",
    "223e4567-e89b-12d3-a456-426614174001"
  ],
  "connection": "LOKAL",
  "days_back": 2,
  "limit": 100
}
```

**Response:**

```json
{
  "success": true,
  "data": {
    "connection": "LOKAL",
    "time_range": {
      "from": "2024-01-01T00:00:00.000Z",
      "to": "2024-01-08T00:00:00.000Z",
      "days_back": 7
    },
    "total_uuids": 2,
    "successful_queries": 2,
    "failed_queries": 0,
    "errors": null,
    "results": {
      "uuid1": {
        "success": true,
        "total_messages": 10,
        "messages": [...],
        "successful_queries": [...],
        "search_info": {...}
      }
    }
  }
}
```

#### Search Graylog Data

**POST** `/api/graylog/data/search`

Search Graylog with custom query.

**Request Body:**

```json
{
  "query": "error AND level:ERROR",
  "connection": "LOKAL",
  "days_back": 2,
  "limit": 100
}
```

#### Get Graylog Config

**GET** `/api/graylog/data/config`

Get current Graylog configuration.

**Query Parameters:**

- `connection` (optional, default: "LOKAL") - Connection name

**Response:**

```json
{
  "success": true,
  "data": {
    "connection": "LOKAL",
    "config": {
      "GRAYLOG_BASE_URL": "http://10.132.6.4:9000",
      "GRAYLOG_USERNAME": "admindev",
      "GRAYLOG_PASSWORD": "***"
    }
  }
}
```

### Graylog Config

#### Base Path: `/api/graylog/config`

#### Get All Configurations

**GET** `/api/graylog/config/configurations`

Get all Graylog configurations grouped by environment.

**Response:**

```json
{
  "success": true,
  "configurations": [
    {
      "environment": "GRAYLOG_PRODUCTION",
      "config": {
        "BASE_URL": "http://prod.graylog:9000",
        "USERNAME": "admin",
        "PASSWORD": "password"
      }
    },
    {
      "environment": "GRAYLOG_DEVELOPMENT",
      "config": {
        "BASE_URL": "http://dev.graylog:9000",
        "USERNAME": "dev",
        "PASSWORD": "password"
      }
    }
  ]
}
```

#### Get Configuration by Environment

**GET** `/api/graylog/config/configurations/<environment>`

Get Graylog configuration for a specific environment.

**Response:**

```json
{
  "success": true,
  "configuration": {
    "environment": "GRAYLOG_PRODUCTION",
    "config": {
      "BASE_URL": "http://prod.graylog:9000",
      "USERNAME": "admin",
      "PASSWORD": "password"
    }
  }
}
```

#### Create Configuration

**POST** `/api/graylog/config/configurations`

Create a new Graylog configuration.

**Request Body:**

```json
{
  "environment": "GRAYLOG_PRODUCTION",
  "config": {
    "BASE_URL": "http://prod.graylog:9000",
    "USERNAME": "admin",
    "PASSWORD": "password"
  }
}
```

**Response:**

```json
{
  "success": true,
  "message": "Graylog configuration for GRAYLOG_PRODUCTION created successfully",
  "inserted_ids": [1, 2, 3]
}
```

#### Update Configuration

**PUT** `/api/graylog/config/configurations/<environment>`

Update Graylog configuration for a specific environment.

**Request Body:**

```json
{
  "config": {
    "BASE_URL": "http://new.graylog:9000",
    "USERNAME": "newuser",
    "PASSWORD": "newpassword"
  }
}
```

#### Delete Configuration

**DELETE** `/api/graylog/config/configurations/<environment>`

Delete Graylog configuration for a specific environment.

#### Update Configuration Field

**PUT** `/api/graylog/config/configurations/<environment>/<field_name>`

Update a specific field in Graylog configuration.

**Request Body:**

```json
{
  "value": "http://new.graylog:9000"
}
```

**Example:**

```
PUT /api/graylog/config/configurations/GRAYLOG_PRODUCTION/BASE_URL
```

#### Get Environments

**GET** `/api/graylog/config/environments`

Get list of available Graylog environments.

**Response:**

```json
{
  "success": true,
  "environments": ["GRAYLOG_PRODUCTION", "GRAYLOG_DEVELOPMENT"]
}
```

### Graylog Rules

#### Base Path: `/api/graylog/rules`

#### Get All Rules

**GET** `/api/graylog/rules`

Get all Graylog rules.

**Query Parameters:**

- `show_inactive` (optional, default: "true") - Include inactive rules

**Response:**

```json
{
  "success": true,
  "rules": [
    {
      "id": 1,
      "key_word": "error|exception|failed",
      "action": "background-color: #ffcccc; color: #990000;",
      "active": 1,
      "rules_mode": "all",
      "enable_link": 0,
      "url": "",
      "extra_attributes": "{}",
      "description": null
    }
  ]
}
```

#### Create Rule

**POST** `/api/graylog/rules`

Add a new Graylog rule.

**Request Body:**

```json
{
  "key_word": "error|exception",
  "action": "background-color: #ffcccc; color: #990000;",
  "active": true,
  "rules_mode": "all",
  "enable_link": false,
  "url": "",
  "extra_attributes": "{}"
}
```

**Response:**

```json
{
  "success": true,
  "message": "Rule added successfully",
  "rule": {
    "id": 1,
    "key_word": "error|exception",
    "action": "background-color: #ffcccc; color: #990000;",
    "active": true,
    "rules_mode": "all",
    "enable_link": false,
    "url": "",
    "extra_attributes": "{}"
  }
}
```

#### Update Rule

**PUT** `/api/graylog/rules/<rule_id>`

Update an existing Graylog rule.

**Request Body:**

```json
{
  "key_word": "error|exception",
  "action": "background-color: #ffcccc; color: #990000;",
  "active": true,
  "rules_mode": "all",
  "enable_link": false,
  "url": "",
  "extra_attributes": "{}"
}
```

#### Delete Rule

**DELETE** `/api/graylog/rules/<rule_id>`

Delete a Graylog rule.

**Response:**

```json
{
  "success": true,
  "message": "Rule deleted successfully",
  "rule": {
    "id": 1,
    "key_word": "error|exception",
    "action": "background-color: #ffcccc; color: #990000;",
    "active": 1,
    "rules_mode": "all",
    "enable_link": 0,
    "url": "",
    "extra_attributes": "{}"
  }
}
```

#### Replace Rule

**POST** `/api/graylog/rules/replace`

Replace an existing rule with the same key_word (upsert).

**Request Body:**

```json
{
  "key_word": "error|exception",
  "action": "background-color: #ffcccc; color: #990000;",
  "active": true,
  "rules_mode": "all",
  "enable_link": false,
  "url": "",
  "extra_attributes": "{}"
}
```

#### Initialize Default Rules

**POST** `/api/graylog/rules/init-defaults`

Initialize default rules if the table is empty.

**Response:**

```json
{
  "success": true,
  "message": "Default rules initialized successfully",
  "rules": [...]
}
```

---

## Transaction APIs

### Base Path: `/api/transactions`

#### Get Transactions

**GET** `/api/transactions`

Get list of transactions.

**Query Parameters:**

- Various filtering and pagination parameters

#### Create Transaction

**POST** `/api/transactions`

Create a new transaction.

#### Export Transactions (CSV)

**POST** `/api/transactions/transactions/export/csv`

Export transactions to CSV format.

**Request Body:**

```json
{
  "filters": {...},
  "columns": [...]
}
```

#### Export Transactions (Excel)

**POST** `/api/transactions/transactions/export/excel`

Export transactions to Excel format.

#### Export Transactions (PDF)

**POST** `/api/transactions/transactions/export/pdf`

Export transactions to PDF format.

#### Export Transactions (HTML)

**POST** `/api/transactions/transactions/export/html`

Export transactions to HTML format.

#### Get Transaction Profiles

**GET** `/api/transactions/profiles`

Get transaction export profiles.

#### Get Transaction Attributes

**GET** `/api/transactions/<trx_id>/attributes`

Get attributes for a specific transaction.

#### Get Transaction Endpoints

**GET** `/api/transactions/<uuid>/endpoints`

Get endpoints for a transaction UUID.

### Transaction Utilities

#### Base Path: `/api/transaction`

#### Save Transaction

**POST** `/api/transaction/save`

Save a transaction.

**Request Body:**

```json
{
  "transaction_data": {...}
}
```

#### Get Saved Transactions

**GET** `/api/transaction/save`

Get list of saved transactions.

#### Get Saved Transaction

**GET** `/api/transaction/save/<saved_id>`

Get a specific saved transaction.

#### Delete Saved Transaction

**DELETE** `/api/transaction/save/<saved_id>`

Delete a saved transaction.

#### Export Transaction (HTML)

**POST** `/api/transaction/export/html`

Export transaction to HTML.

#### Export Selected Transactions

**POST** `/api/transaction/export/selected`

Export selected transactions.

---

## Gateway APIs

### Base Path: `/api/gateway`

#### Get Gateways

**GET** `/api/gateway`

Get list of all gateways.

#### Create Gateway

**POST** `/api/gateway`

Create a new gateway.

**Request Body:**

```json
{
  "name": "string",
  "description": "string",
  "config": {...}
}
```

#### Create New Gateway

**POST** `/api/gateway/new`

Create a new gateway with extended options.

#### Get Gateway

**GET** `/api/gateway/<gateway_id>`

Get a specific gateway by ID.

#### Update Gateway

**PUT** `/api/gateway/<gateway_id>`

Update gateway information.

#### Delete Gateway

**DELETE** `/api/gateway/<gateway_id>`

Delete a gateway.

#### Get Gateway Bins

**GET** `/api/gateway/<gateway_id>/bins`

Get bins for a gateway.

#### Create Gateway Bin

**POST** `/api/gateway/<gateway_id>/bins`

Create a new bin for a gateway.

#### Create New Bin

**POST** `/api/gateway/<gateway_id>/bins/new`

Create a new bin with extended options.

#### Get Bin

**GET** `/api/gateway/bins/<bin_id>`

Get a specific bin by ID.

#### Update Bin

**PUT** `/api/gateway/bins/<bin_id>`

Update bin information.

#### Delete Bin

**DELETE** `/api/gateway/bins/<bin_id>`

Delete a bin.

#### Search Gateways

**GET** `/api/gateway/search`

Search gateways.

**Query Parameters:**

- `q` - Search query

#### Move Bins

**POST** `/api/gateway/bins/move`

Move bins between gateways.

**Request Body:**

```json
{
  "bin_ids": [1, 2, 3],
  "target_gateway_id": 5
}
```

---

## Participant APIs

### Base Path: `/api/participant`

#### Create Participant

**POST** `/api/participant`

Create a new participant.

**Request Body:**

```json
{
  "name": "string",
  "code": "string",
  "config": {...}
}
```

#### Container Plugin

**POST** `/api/participant/containerPlugin`

Handle container plugin operations.

#### Get Duplicate Info

**POST** `/api/participant/duplicate-info`

Get information about duplicate participants.

**Request Body:**

```json
{
  "participant_id": 1
}
```

#### Duplicate Participant

**POST** `/api/participant/duplicate`

Duplicate a participant.

**Request Body:**

```json
{
  "participant_id": 1,
  "new_name": "string"
}
```

---

## Rules APIs

### Base Path: `/api/rules`

#### Get Rule Sets

**GET** `/api/rules`

Get list of all rule sets.

#### Get Rule Set

**GET** `/api/rules/<rule_set_id>`

Get a specific rule set.

#### Export Rules

**GET** `/api/rules/<rule_set_id>/export`

Export rules from a rule set.

---

## Routing APIs

### Base Path: `/api/routing`

#### Get Routes

**GET** `/api/routing`

Get list of routing rules.

#### Create Route

**POST** `/api/routing`

Create a new routing rule.

#### Update Route

**PUT** `/api/routing/<route_id>`

Update a routing rule.

#### Delete Route

**DELETE** `/api/routing/<route_id>`

Delete a routing rule.

### Routing Simulation

#### Base Path: `/api/routing-sim`

#### Simulate Routing

**POST** `/api/routing-sim`

Simulate routing for a transaction.

**Request Body:**

```json
{
  "transaction_data": {...},
  "routing_rules": [...]
}
```

### Bin Routing

#### Base Path: `/api/bin-routing`

#### Get Bin Routes

**GET** `/api/bin-routing`

Get bin routing rules.

#### Create Bin Route

**POST** `/api/bin-routing`

Create a new bin routing rule.

---

## SSH APIs

### SSH Log Extractor

#### Base Path: `/api/ssh/extractor`

#### Extract Logs

**POST** `/api/ssh/extractor`

Extract logs from SSH server.

**Request Body:**

```json
{
  "connection": "string",
  "log_path": "/var/log/app.log",
  "filters": {...}
}
```

### SSH Client

#### Base Path: `/api/ssh/client`

#### Get Connections

**GET** `/api/ssh/client/connections`

Get list of SSH connections.

#### Get Server Status

**GET** `/api/ssh/client/status`

Get SSH server status.

### SSH Transfer

#### Base Path: `/api/ssh/transfer`

#### Transfer File

**POST** `/api/ssh/transfer`

Transfer file via SSH.

**Request Body:**

```json
{
  "connection": "string",
  "source_path": "/local/path",
  "destination_path": "/remote/path"
}
```

#### Transfer Multiple Files

**POST** `/api/ssh/transfer/multiple`

Transfer multiple files.

**Request Body:**

```json
{
  "connection": "string",
  "files": [
    {
      "source": "/local/path1",
      "destination": "/remote/path1"
    }
  ]
}
```

#### Get Transfer Progress

**GET** `/api/ssh/transfer/progress/<transfer_id>`

Get progress of a file transfer.

#### Pause Transfer

**POST** `/api/ssh/transfer/pause/<transfer_id>`

Pause a file transfer.

#### Resume Transfer

**POST** `/api/ssh/transfer/resume/<transfer_id>`

Resume a paused transfer.

#### Cancel Transfer

**POST** `/api/ssh/transfer/cancel/<transfer_id>`

Cancel a file transfer.

#### Get Active Transfers

**GET** `/api/ssh/transfer/active`

Get list of active transfers.

#### Browse Remote Directory

**POST** `/api/ssh/transfer/browse-remote`

Browse remote directory.

**Request Body:**

```json
{
  "connection": "string",
  "path": "/remote/path"
}
```

#### Browse Local Directory

**POST** `/api/ssh/transfer/browse-local`

Browse local directory.

**Request Body:**

```json
{
  "path": "/local/path"
}
```

#### Verify MD5

**POST** `/api/ssh/transfer/verify-md5`

Verify file MD5 checksum.

**Request Body:**

```json
{
  "connection": "string",
  "file_path": "/remote/path/file.txt"
}
```

#### Cleanup Transfers

**POST** `/api/ssh/transfer/cleanup`

Cleanup completed transfers.

### File Manager

#### Base Path: `/api/ssh/file-manager`

#### List Files

**POST** `/api/ssh/file-manager`

List files in a directory.

**Request Body:**

```json
{
  "connection": "string",
  "path": "/remote/path"
}
```

#### Get Disk Usage

**POST** `/api/ssh/file-manager/disk-usage`

Get disk usage information.

**Request Body:**

```json
{
  "connection": "string",
  "path": "/remote/path"
}
```

#### Download File

**POST** `/api/ssh/file-manager/download`

Download a file.

**Request Body:**

```json
{
  "connection": "string",
  "file_path": "/remote/path/file.txt"
}
```

#### Bulk Download

**POST** `/api/ssh/file-manager/bulk-download`

Download multiple files.

**Request Body:**

```json
{
  "connection": "string",
  "files": ["/remote/path/file1.txt", "/remote/path/file2.txt"]
}
```

#### Rename File

**POST** `/api/ssh/file-manager/rename`

Rename a file or directory.

**Request Body:**

```json
{
  "connection": "string",
  "old_path": "/remote/path/old.txt",
  "new_path": "/remote/path/new.txt"
}
```

#### Delete File

**POST** `/api/ssh/file-manager/delete`

Delete a file or directory.

**Request Body:**

```json
{
  "connection": "string",
  "path": "/remote/path/file.txt"
}
```

#### Create Folder

**POST** `/api/ssh/file-manager/create-folder`

Create a new folder.

**Request Body:**

```json
{
  "connection": "string",
  "path": "/remote/path/newfolder"
}
```

#### Upload File

**POST** `/api/ssh/file-manager/upload`

Upload a file.

**Request Body:**

```json
{
  "connection": "string",
  "destination_path": "/remote/path",
  "file": "file_data"
}
```

#### Get Connections

**GET** `/api/ssh/file-manager/connections`

Get list of SSH connections.

---

## Network Message APIs

### Base Path: `/api/network-message`

#### Get Packets

**GET** `/api/network-message`

Get list of saved network packets.

**Query Parameters:**

- `limit` - Maximum number of packets
- `offset` - Offset for pagination
- `folder` - Filter by folder
- `protocol` - Filter by protocol

#### Send Message

**POST** `/api/network-message/send`

Send a network message.

**Request Body:**

```json
{
  "connection": "string",
  "message": "hex_string",
  "protocol": "ISO8583"
}
```

#### Send Packet

**POST** `/api/network-message/<packet_id>/send`

Send a saved packet.

#### Get Traffic Logs

**POST** `/api/network-message/traffic-logs`

Get traffic logs.

**Request Body:**

```json
{
  "connection": "string",
  "filters": {...}
}
```

#### Clear Traffic Logs

**POST** `/api/network-message/traffic-logs/clear`

Clear traffic logs.

#### Get Connection Logs

**POST** `/api/network-message/connection-logs`

Get connection logs.

#### Clear Connection Logs

**POST** `/api/network-message/connection-logs/clear`

Clear connection logs.

#### Close Connections

**POST** `/api/network-message/connections/close`

Close network connections.

**Request Body:**

```json
{
  "connection": "string"
}
```

#### Get Status

**GET** `/api/network-message/status`

Get network message service status.

#### Get Packet

**GET** `/api/network-message/<packet_id>`

Get a specific packet by ID.

#### Save Packet

**POST** `/api/network-message`

Save a network packet.

**Request Body:**

```json
{
  "name": "string",
  "message": "hex_string",
  "protocol": "ISO8583",
  "folder": "string",
  "description": "string"
}
```

#### Update Packet

**PUT** `/api/network-message/<packet_id>`

Update a saved packet.

#### Delete Packet

**DELETE** `/api/network-message/<packet_id>`

Delete a packet.

#### Search Packets

**GET** `/api/network-message/search`

Search packets.

**Query Parameters:**

- `q` - Search query

#### Get Packet Count

**GET** `/api/network-message/count`

Get total count of packets.

#### Get Folders

**GET** `/api/network-message/folders`

Get list of folders.

#### Get Folder Packets

**GET** `/api/network-message/folder/<folder>`

Get packets in a folder.

#### Get Protocol Packets

**GET** `/api/network-message/protocol/<protocol>`

Get packets by protocol.

---

## Redmine APIs

### Base Path: `/api/redmine`

#### Get Issue

**GET** `/api/redmine/issue/<ticket_number>`

Get Redmine issue information.

**Response:**

```json
{
  "success": true,
  "data": {
    "ticket_number": "12345",
    "subject": "Issue Title",
    "description": "Issue description",
    "status": "Open",
    "priority": "High",
    "assignee": "John Doe",
    "created_on": "2024-01-01",
    "updated_on": "2024-01-02"
  }
}
```

#### Test Connection

**GET** `/api/redmine/test`

Test Redmine connection.

**Response:**

```json
{
  "success": true,
  "message": "Connection successful"
}
```

---

## Query Management APIs

### Base Path: `/api/queries`

#### Get Queries

**GET** `/api/queries`

Get list of all queries.

**Query Parameters:**

- `search` - Search term
- `limit` - Maximum results
- `offset` - Offset for pagination

#### Get Query

**GET** `/api/queries/<query_id>`

Get a specific query by ID.

#### Create Query

**POST** `/api/queries`

Create a new query.

**Request Body:**

```json
{
  "name": "string",
  "query": "SELECT * FROM table",
  "connection_name": "string",
  "description": "string"
}
```

#### Update Query

**PUT** `/api/queries/<query_id>`

Update a query.

#### Delete Query

**DELETE** `/api/queries/<query_id>`

Delete a query.

#### Execute Query

**POST** `/api/queries/<query_id>/execute`

Execute a saved query.

**Request Body:**

```json
{
  "parameters": {...}
}
```

#### Execute Raw Query

**POST** `/api/queries/execute-raw`

Execute a raw SQL query.

**Request Body:**

```json
{
  "query": "SELECT * FROM table",
  "connection_name": "string"
}
```

#### Search Queries

**GET** `/api/queries/search`

Search queries.

**Query Parameters:**

- `q` - Search query

#### AI Query Assistant

**POST** `/api/queries/ai`

Get AI assistance for query generation.

**Request Body:**

```json
{
  "prompt": "Find all users with active status",
  "connection_name": "string"
}
```

---

## Error Handling

All API endpoints follow a consistent error response format:

```json
{
  "success": false,
  "error": "Error message description"
}
```

### Common Error Scenarios

1. **400 Bad Request** - Invalid request parameters or missing required fields
2. **401 Unauthorized** - Missing or invalid authentication token
3. **403 Forbidden** - Insufficient permissions
4. **404 Not Found** - Resource not found
5. **409 Conflict** - Resource conflict (e.g., duplicate entry)
6. **500 Internal Server Error** - Server-side error

### Example Error Response

```json
{
  "success": false,
  "error": "UUID parameter is required"
}
```

---

## CORS Support

All API endpoints support CORS (Cross-Origin Resource Sharing) with the following configuration:

- **Access-Control-Allow-Origin**: Origin from request header or `*`
- **Access-Control-Allow-Methods**: `GET, POST, PUT, DELETE, OPTIONS`
- **Access-Control-Allow-Headers**: `Content-Type, Accept, Authorization, ngrok-skip-browser-warning`
- **Access-Control-Allow-Credentials**: `true`

### Preflight Requests

All endpoints support OPTIONS requests for CORS preflight:

```
OPTIONS /api/<endpoint>
```

**Response:**

```json
{
  "status": "ok"
}
```

---

## Notes

1. **Authentication**: Most endpoints require JWT authentication. Include the token in the Authorization header: `Authorization: Bearer <token>`

2. **Date Formats**: All dates are in ISO 8601 format (e.g., `2024-01-01T00:00:00.000Z`)

3. **Pagination**: Endpoints that support pagination use `limit` and `offset` query parameters

4. **File Uploads**: File upload endpoints accept multipart/form-data

5. **Rate Limiting**: Currently no rate limiting is implemented, but it's recommended for production use

6. **SSL Verification**: Some endpoints may disable SSL verification for development environments

---

## Version History

- **v1.0** - Initial API documentation
  - Complete coverage of all endpoints
  - Authentication and authorization
  - Core, Graylog, Transaction, Gateway, and other APIs

---

## Support

For API support or questions, please contact the development team.
