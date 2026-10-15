from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session
import os
import json
from app.models.rna_rules_model import (
    get_rule_sets, get_rule_set, create_rule_set, update_rule_set, delete_rule_set,
    create_rule, update_rule, delete_rule, save_rule_conditions,
    export_rule_set_to_file, import_rules_from_file
)

rna_rules_bp = Blueprint('rna_rules', __name__)

@rna_rules_bp.route('/')
def index():
    """Main page for the RNA Rules Builder"""
    rule_sets = get_rule_sets()
    return render_template('rna_rules/index.html', rule_sets=rule_sets)

@rna_rules_bp.route('/builder/<int:rule_set_id>')
def builder(rule_set_id):
    """Rule builder page for a specific rule set"""
    rule_set = get_rule_set(rule_set_id)
    if not rule_set:
        flash('Rule set not found', 'error')
        return redirect(url_for('rna_rules.index'))
    
    return render_template('rna_rules/builder.html', rule_set=rule_set)

@rna_rules_bp.route('/create', methods=['POST'])
def create():
    """Create a new rule set"""
    name = request.form.get('name', '')
    description = request.form.get('description', '')
    
    if not name:
        flash('Rule set name is required', 'error')
        return redirect(url_for('rna_rules.index'))
    
    rule_set_id = create_rule_set(name, description)
    flash(f'Rule set "{name}" created successfully', 'success')
    return redirect(url_for('rna_rules.builder', rule_set_id=rule_set_id))

@rna_rules_bp.route('/update/<int:rule_set_id>', methods=['POST'])
def update(rule_set_id):
    """Update a rule set"""
    name = request.form.get('name', '')
    description = request.form.get('description', '')
    is_active = request.form.get('is_active', '0') == '1'
    
    if not name:
        flash('Rule set name is required', 'error')
        return redirect(url_for('rna_rules.builder', rule_set_id=rule_set_id))
    
    success = update_rule_set(rule_set_id, name, description, is_active)
    if success:
        flash(f'Rule set updated successfully', 'success')
    else:
        flash('Failed to update rule set', 'error')
    
    return redirect(url_for('rna_rules.builder', rule_set_id=rule_set_id))

@rna_rules_bp.route('/delete/<int:rule_set_id>', methods=['POST'])
def delete(rule_set_id):
    """Delete a rule set"""
    success = delete_rule_set(rule_set_id)
    if success:
        flash('Rule set deleted successfully', 'success')
    else:
        flash('Failed to delete rule set', 'error')
    
    return redirect(url_for('rna_rules.index'))

@rna_rules_bp.route('/import', methods=['POST'])
def import_rules():
    """Import rules from a file"""
    if 'rules_file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('rna_rules.index'))
    
    file = request.files['rules_file']
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('rna_rules.index'))
    
    if file:
        # Save the file temporarily
        temp_path = os.path.join('tmp', file.filename)
        file.save(temp_path)
        
        # Import the rules
        rule_set_name = request.form.get('rule_set_name', '')
        rule_set_id = import_rules_from_file(temp_path, rule_set_name)
        
        # Clean up
        os.remove(temp_path)
        
        if rule_set_id:
            flash('Rules imported successfully', 'success')
            return redirect(url_for('rna_rules.builder', rule_set_id=rule_set_id))
        else:
            flash('Failed to import rules', 'error')
    
    return redirect(url_for('rna_rules.index'))

@rna_rules_bp.route('/export/<int:rule_set_id>', methods=['POST'])
def export_rules(rule_set_id):
    """Export rules to a file"""
    rule_set = get_rule_set(rule_set_id)
    if not rule_set:
        flash('Rule set not found', 'error')
        return redirect(url_for('rna_rules.index'))
    
    # Export to a temporary file
    temp_path = os.path.join('tmp', f'rules_{rule_set_id}.txt')
    success = export_rule_set_to_file(rule_set_id, temp_path)
    
    if success:
        with open(temp_path, 'r') as f:
            content = f.read()
        
        # Clean up
        os.remove(temp_path)
        
        response = jsonify({
            'success': True,
            'content': content,
            'filename': f'rules_{rule_set["name"]}.txt'
        })
        return response
    else:
        return jsonify({'success': False, 'message': 'Failed to export rules'})

# API Endpoints for AJAX operations

@rna_rules_bp.route('/api/rule_sets', methods=['GET'])
def api_get_rule_sets():
    """Get all rule sets as JSON"""
    rule_sets = get_rule_sets()
    return jsonify(rule_sets)

@rna_rules_bp.route('/api/rule_set/<int:rule_set_id>', methods=['GET'])
def api_get_rule_set(rule_set_id):
    """Get a specific rule set as JSON"""
    rule_set = get_rule_set(rule_set_id)
    if rule_set:
        return jsonify(rule_set)
    return jsonify({'error': 'Rule set not found'}), 404

@rna_rules_bp.route('/api/rule/<int:rule_set_id>', methods=['POST'])
def api_create_rule(rule_set_id):
    """Create a new rule in a rule set"""
    data = request.json
    
    rule_type = data.get('rule_type', 'condition')
    rule_json = data.get('rule_json', {})
    order_position = data.get('order_position', 0)
    
    rule_id = create_rule(rule_set_id, rule_type, rule_json, order_position)
    
    if rule_id:
        # If conditions are provided, save them
        conditions = data.get('conditions', [])
        if conditions:
            save_rule_conditions(rule_id, conditions)
        
        return jsonify({'success': True, 'rule_id': rule_id})
    
    return jsonify({'success': False, 'message': 'Failed to create rule'})

@rna_rules_bp.route('/api/rule/<int:rule_id>', methods=['PUT'])
def api_update_rule(rule_id):
    """Update a rule"""
    data = request.json
    
    rule_type = data.get('rule_type', 'condition')
    rule_json = data.get('rule_json', {})
    order_position = data.get('order_position', 0)
    
    success = update_rule(rule_id, rule_type, rule_json, order_position)
    
    if success:
        # If conditions are provided, update them
        conditions = data.get('conditions', [])
        if conditions:
            save_rule_conditions(rule_id, conditions)
        
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'message': 'Failed to update rule'})

@rna_rules_bp.route('/api/rule/<int:rule_id>', methods=['DELETE'])
def api_delete_rule(rule_id):
    """Delete a rule"""
    success = delete_rule(rule_id)
    
    if success:
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'message': 'Failed to delete rule'})

@rna_rules_bp.route('/api/rule_sets/<int:rule_set_id>/rules/reorder', methods=['POST'])
def api_reorder_rules(rule_set_id):
    """Reorder rules in a rule set"""
    data = request.json
    
    rule_ids = data.get('rule_ids', [])
    
    for i, rule_id in enumerate(rule_ids):
        update_rule(rule_id, None, None, i)
    
    return jsonify({'success': True})

@rna_rules_bp.route('/api/parse_rule', methods=['POST'])
def api_parse_rule():
    """Parse a rule from text format"""
    data = request.json
    
    rule_text = data.get('rule_text', '')
    
    from app.models.rna_rules_model import parse_rule_text
    parsed = parse_rule_text(rule_text)
    
    return jsonify({
        'success': True,
        'parsed': parsed,
        'rule_text': rule_text
    })

@rna_rules_bp.route('/workflow_builder/<int:rule_set_id>')
def workflow_builder(rule_set_id):
    """Visual workflow builder for a specific rule set"""
    rule_set = get_rule_set(rule_set_id)
    if not rule_set:
        flash('Rule set not found', 'error')
        return redirect(url_for('rna_rules.index'))
    
    # Get workflow data if it exists
    workflow_data = None
    if 'workflow_data' in rule_set:
        workflow_data = rule_set['workflow_data']
    
    return render_template('rna_rules/workflow_builder.html', rule_set=rule_set, workflow_data=workflow_data)

@rna_rules_bp.route('/api/save_workflow/<int:rule_set_id>', methods=['POST'])
def api_save_workflow(rule_set_id):
    """Save workflow data for a rule set"""
    rule_set = get_rule_set(rule_set_id)
    if not rule_set:
        return jsonify({'success': False, 'message': 'Rule set not found'})
    
    workflow_data = request.json
    
    # Save workflow data
    from app.models.rna_rules_model import save_workflow_data
    success = save_workflow_data(rule_set_id, workflow_data)
    
    if success:
        # Create or update the rule with the generated rule text
        rule_text = workflow_data.get('generatedRule', '')
        if rule_text:
            # Check if there's an existing workflow rule
            workflow_rule = None
            for rule in rule_set['rules']:
                if rule.get('source') == 'workflow':
                    workflow_rule = rule
                    break
            
            rule_json = {
                'rule_text': rule_text,
                'source': 'workflow'
            }
            
            if workflow_rule:
                # Update existing rule
                update_rule(workflow_rule['id'], 'condition', rule_json, workflow_rule['order_position'])
            else:
                # Create new rule
                create_rule(rule_set_id, 'condition', rule_json, len(rule_set['rules']))
        
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'message': 'Failed to save workflow data'}) 

@rna_rules_bp.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({'status': 'ok'})
    origin = request.headers.get('Origin', '*')
    response.headers.add('Access-Control-Allow-Origin', origin)
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept, ngrok-skip-browser-warning')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response, 200 